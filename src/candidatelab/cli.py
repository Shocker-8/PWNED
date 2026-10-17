"""Scriptable commands over shared configuration and application services."""
import argparse
import json
from dataclasses import asdict
from pathlib import Path
import sys
from . import __version__
from .config import Config, parse_wordlist_paths
from .engine import run
from .presentation import Presentation, safe


def main(argv=None) -> int:
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    plain, no_color = "--plain" in raw_args, "--no-color" in raw_args

    class CLIParser(argparse.ArgumentParser):
        def print_help(self, file=None):
            Presentation(file if file is not None else sys.stdout, plain=plain, no_color=no_color).help(self.format_help())

        def error(self, message):
            self.print_usage(sys.stderr)
            self.exit(2, f"{self.prog}: error: {safe(message)}\n")

    def presentation_options(target):
        target.add_argument("--plain", action="store_true", default=argparse.SUPPRESS, help="no styles, banners or terminal controls")
        target.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="disable colors")

    parser = CLIParser(prog="candidatelab")
    presentation_options(parser)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    def generation_arguments(cmd):
        presentation_options(cmd)
        cmd.add_argument("--mode", choices=("passwords", "usernames", "pairs"), default=argparse.SUPPRESS)
        cmd.add_argument("--output-format", choices=("text", "jsonl"), default=argparse.SUPPRESS)
        cmd.add_argument("--preset", choices=("generic", "wpa2"), default=argparse.SUPPRESS)
        cmd.add_argument("--config", help="versioned JSON; relative paths resolve beside it")
        cmd.add_argument("--input", action="append", default=argparse.SUPPRESS, help="wordlist paths, comma-separated or repeatable; replaces configured sources")
        cmd.add_argument("--output", default=argparse.SUPPRESS)
        for flag in ("max-candidates", "max-attempts", "max-output-bytes", "min-length", "max-length", "max-input-records"):
            cmd.add_argument(f"--{flag}", type=int, default=argparse.SUPPRESS)
        cmd.add_argument("--max-seconds", type=float, default=argparse.SUPPRESS, help="cooperative generation time budget; 0 stops immediately")
        cmd.add_argument("--priority", action="append", default=argparse.SUPPRESS, help="mutation family, repeatable; original remains first")
        cmd.add_argument("--family-quota", action="append", default=argparse.SUPPRESS, metavar="FAMILY=N", help="maximum admitted attempts for a family; skipped attempts still consume the global budget")
        cmd.add_argument("--source-scheduling", choices=("ordered", "round_robin"), default=argparse.SUPPRESS)
        cmd.add_argument("--provenance", action="store_true", default=argparse.SUPPRESS, help="include source/family metadata in JSONL")
        cmd.add_argument("--seed", action="append", default=argparse.SUPPRESS, help="replace configured seeds; repeatable")

    cmd = commands.add_parser("generate", help="export supplied candidate words")
    generation_arguments(cmd)
    cmd.add_argument("--explain", action="store_true", help="show a bounded plan on stderr before generation")
    preview = commands.add_parser("plan", help="show bounds/settings without scanning wordlists or generating")
    generation_arguments(preview)
    preview.add_argument("--human", action="store_true", help="render a readable terminal plan; default is JSON")
    init = commands.add_parser("init", help="question wizard; save configuration without generating")
    presentation_options(init)
    init.add_argument("--manual", action="store_true", help="use the deterministic form instead of the default local Gemma interview")
    init.add_argument("--runtime-config", default="opencode.json", help="local Gemma configuration")
    init.add_argument("--output", default="candidatelab.json", help="configuration destination")
    interview = commands.add_parser("interview", help="adaptive local Gemma interview and reviewable fact ledger")
    presentation_options(interview)
    interview.add_argument("--kind", choices=("passwords", "usernames", "pairs", "wifi-personal", "wifi-business"), required=True)
    interview.add_argument("--scope", choices=("owned", "consenting-participant"), required=True)
    interview.add_argument("--runtime-config", default="opencode.json")
    interview.add_argument("--output", default="interview.json")
    tui = commands.add_parser("tui", help="launch the early retro TUI shell (requires the ui extra)")
    presentation_options(tui)
    policy = commands.add_parser("policy-audit", help="synthetic password-policy audit with local Gemma")
    presentation_options(policy)
    policy.add_argument("--context", choices=("account", "wifi"), required=True,
                        help="account-password or Wi-Fi-passphrase policy")
    policy.add_argument("--runtime-config", default="opencode.json", help="local Gemma runtime configuration")
    policy.add_argument("--output", default="policy_corpus.txt", help="synthetic corpus destination")
    args = parser.parse_args(raw_args)
    display = Presentation(plain=plain, no_color=no_color)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        if args.command == "tui":
            from . import tui as tui_module
            return tui_module.run(sys.stderr)
        if args.command == "policy-audit":
            if not sys.stdin.isatty():
                raise ValueError("policy-audit requires an interactive terminal")
            from .policyaudit import PolicyGemma, conduct_policy_audit
            display.banner()
            conduct_policy_audit(args.context, args.output, PolicyGemma(args.runtime_config),
                                 display.prompt, display.message)
            return 0
        if args.command == "interview":
            if not sys.stdin.isatty():
                raise ValueError("interview requires an interactive terminal")
            from .interview import LocalGemma, conduct
            display.banner()
            conduct(args.kind, args.scope, args.output, LocalGemma(args.runtime_config), display.prompt, display.message)
            return 0
        if args.command == "init":
            if not sys.stdin.isatty():
                raise ValueError("init requires an interactive terminal; use generate --config PATH for batch work")
            from .wizard import initialize
            display.banner()
            if args.manual:
                initialize(args.output, read=display.prompt, write=display.message)
            else:
                from .interview import guided_init
                guided_init(args.output, args.runtime_config, display.prompt, display.message)
            return 0
        data = asdict(Config.load(args.config)) if args.config else {}
        for key, value in vars(args).items():
            if key == "input":
                data["input"] = None
                data["inputs"] = [path for group in value for path in parse_wordlist_paths(group)]
            elif key == "priority":
                data["priorities"] = value
            elif key == "family_quota":
                quotas = {}
                for item in value:
                    family, separator, count = item.partition("=")
                    if not separator or family in quotas:
                        raise ValueError("use unique --family-quota FAMILY=N entries")
                    quotas[family] = int(count)
                data["family_quotas"] = quotas
            elif key not in ("command", "config", "explain", "plain", "no_color", "human"):
                data["seeds" if key == "seed" else key] = value
        config = Config.from_dict(data)
        if args.command == "plan" or args.explain:
            from .planner import plan
            description = plan(config)
            if args.command == "plan" and not args.human:
                print(json.dumps(description, ensure_ascii=True, indent=2))
            elif args.command == "plan":
                Presentation(sys.stdout, plain=plain, no_color=no_color).plan(description)
            else:
                display.plan(description)
            if args.command == "plan":
                return 0
        result = run(config)
    except KeyboardInterrupt:
        display.text("Cancelled; generation may have left partial output.")
        return 130
    except (OSError, ValueError, EOFError) as exc:
        display.error(exc)
        return 1
    display.summary(config, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
