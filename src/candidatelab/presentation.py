"""Terminal rendering only; candidate values never pass through this renderer."""
import json
import os
import sys
import unicodedata


def safe(value):
    return "".join(char.encode("unicode_escape").decode("ascii")
                   if unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp") else char
                   for char in str(value))


class Presentation:
    def __init__(self, stream=None, *, plain=False, no_color=False):
        self.stream = stream if stream is not None else sys.stderr
        self.console = None
        if not plain and getattr(self.stream, "isatty", lambda: False)() and os.environ.get("TERM") not in ("dumb", "unknown"):
            try:
                from rich.console import Console
                from rich.theme import Theme
                self.console = Console(file=self.stream, force_terminal=True,
                                       no_color=no_color or "NO_COLOR" in os.environ, markup=False, highlight=False,
                                       theme=Theme({"retro.title":"bold bright_green", "retro.border":"red",
                                                    "retro.key":"green", "retro.notice":"yellow", "retro.text":"white"}))
            except ImportError:
                pass

    def banner(self):
        if self.console:
            from rich.panel import Panel
            from rich.text import Text
            self.console.print(Panel(Text("CANDIDATELAB // OFFLINE MUTATION ENGINE", style="retro.title"),
                                     border_style="retro.border", padding=(0,1), style="on #101210"))

    def text(self, value, *, style="retro.text", multiline=False):
        text = "\n".join(safe(line) for line in str(value).split("\n")) if multiline else safe(value)
        if self.console:
            from rich.text import Text
            self.console.print(Text(text, style=style))
        else:
            print(text, file=self.stream)

    def _table(self, title, rows):
        if self.console:
            from rich.table import Table
            from rich.text import Text
            table = Table(title=Text(safe(title), style="retro.title"), border_style="retro.border", show_header=False, expand=True)
            table.add_column(style="retro.key", ratio=1)
            table.add_column(style="retro.text", ratio=3, overflow="fold")
            for key, value in rows:
                table.add_row(Text(safe(key)), Text(safe(value)))
            self.console.print(table)
        else:
            print(safe(title), file=self.stream)
            for key, value in rows:
                print(f"{safe(key)}: {safe(value)}", file=self.stream)

    def message(self, message):
        if self.console and str(message).startswith("{\n"):
            try:
                value=json.loads(message)
            except ValueError:
                pass
            else:
                if isinstance(value,dict) and "version" in value and "output" in value:
                    self._table("CONFIGURATION REVIEW", [
                        ("Mode / preset", f"{value.get('mode')} / {value.get('preset')}"),
                        ("Languages", ", ".join(value.get("languages",[]))), ("Output",value["output"]),
                        ("Password lengths",f"{value.get('min_length')}–{value.get('max_length')}"),
                        ("Candidate / attempt caps",f"{value.get('max_candidates')} / {value.get('max_attempts')}"),
                        ("Profiles / supplied words",f"{len(value.get('profiles',[]))} / {len(value.get('seeds',[]))}")])
                    return
        self.text(message,multiline=True)

    def prompt(self, question):
        if self.console:
            from rich.text import Text
            self.console.print(Text(safe(question),style="retro.key"),end=" ")
        else:
            print(safe(question),end=" ",file=self.stream,flush=True)
        return input()

    def help(self, text):
        self.banner()
        self.text(text,multiline=True)

    def error(self, message):
        self.text(f"Error: {message}",style="retro.notice")

    def plan(self, value):
        self.banner()
        bounds=value["effective_password_lengths"]
        self._table("GENERATION PLAN", [
            ("Mode / preset",f"{value['mode']} / {value['preset']}"),("Output",value["output"]),
            ("Password characters",f"{bounds['minimum']}–{bounds['maximum']}" if bounds else "not applicable"),
            ("Raw expansion upper bound",value["raw_expansion_upper"] if value["raw_expansion_upper"] is not None else "unknown; wordlists not scanned"),
            ("Emitted upper bound",value["emitted_upper"]),
            ("Attempt / byte caps",f"{value['budgets']['attempts']} / {value['budgets']['bytes']}"),
            ("Time limit / ETA",f"{str(value['budgets']['seconds']) + ' seconds' if value['budgets']['seconds'] is not None else 'not set'} / unknown (not calibrated)"),
            ("Mutation order",", ".join(value["custom_priority_tiers"])),
            ("Colliding usernames",len(value["username_collisions"]))])
        self.text(value["estimate_notes"],style="retro.notice")

    def summary(self, config, result):
        if not self.console:
            self.text(f"Exported {result.accepted} {config.mode} candidates to {config.output}; "
                      f"attempted={result.attempted}, duplicates={result.duplicates}, filtered={result.filtered}, "
                      f"quota_skipped={result.quota_skipped}, bytes={result.output_bytes}, stop={result.stop_reason}, "
                      f"username_collisions={result.username_collision_count}, sha256={result.sha256}")
            return
        self._table("EXPORT RESULT", [
            ("Stop reason",result.stop_reason),("Exported records",result.accepted),
            ("Attempted / duplicate / filtered",f"{result.attempted} / {result.duplicates} / {result.filtered}"),
            ("Quota-skipped / input records",f"{result.quota_skipped} / {result.input_records}"),
            ("Bytes",result.output_bytes),("Elapsed seconds",f"{result.elapsed_seconds:.3f}"),
            ("Output",config.output),("SHA-256",result.sha256)])
