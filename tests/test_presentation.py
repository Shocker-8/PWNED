from contextlib import redirect_stderr,redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from candidatelab.cli import main
from candidatelab.presentation import Presentation,safe

class Terminal(io.StringIO):
    def isatty(self): return True

class PlainPresentationTests(unittest.TestCase):
    def test_safe_displays_control_sequences_without_executing_markup(self):
        value='[red]name[/red]\x1b]52;c;secret\x07\u202e'
        rendered=safe(value)
        self.assertNotIn('\x1b',rendered)
        self.assertNotIn('\x07',rendered)
        self.assertNotIn('\u202e',rendered)
        self.assertIn('[red]name[/red]',rendered)

    def test_plain_has_no_banner_or_control_sequences_even_on_a_terminal(self):
        stream=Terminal()
        view=Presentation(stream,plain=True)
        view.help('usage: example\n[red]literal\x1b[2J')
        self.assertNotIn('\x1b',stream.getvalue())
        self.assertNotIn('OFFLINE MUTATION ENGINE',stream.getvalue())
        self.assertIn('[red]literal',stream.getvalue())

    def test_pipe_does_not_force_color(self):
        stream=io.StringIO()
        with patch.dict(os.environ,{'FORCE_COLOR':'1','TERM':'xterm-256color'}):
            view=Presentation(stream)
            view.help('usage: example')
        self.assertIsNone(view.console)
        self.assertNotIn('\x1b',stream.getvalue())

    def test_plain_flags_before_and_after_subcommand_are_not_config_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            for i,args in enumerate((['--plain','generate'],['generate','--plain'],['--no-color','generate'])):
                output=Path(folder)/str(i)
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(main(args+['--seed','word','--output',str(output)]),0)
                self.assertEqual(output.read_bytes(),b'word\n')

    def test_cli_errors_escape_untrusted_terminal_controls(self):
        stream=io.StringIO()
        with redirect_stderr(stream),self.assertRaises(SystemExit):
            main(['--bad\x1b[2J'])
        self.assertNotIn('\x1b',stream.getvalue())

    def test_machine_plan_stays_json(self):
        output=Terminal()
        with redirect_stdout(output),redirect_stderr(io.StringIO()):
            self.assertEqual(main(['plan','--seed','word']),0)
        self.assertEqual(json.loads(output.getvalue())['mode'],'passwords')
        self.assertNotIn('\x1b',output.getvalue())

@unittest.skipUnless(importlib.util.find_spec('rich'), 'Rich not installed')
class RichPresentationTests(unittest.TestCase):
    def test_retro_renderer_is_active_on_terminal(self):
        stream=Terminal()
        with patch.dict(os.environ,{'TERM':'xterm-256color'}):
            view=Presentation(stream)
            self.assertIsNotNone(view.console)
            view.help('usage: example')
        self.assertIn('CANDIDATELAB',stream.getvalue())
        self.assertIn('\x1b[',stream.getvalue())

    def test_no_color_and_environment_remove_colors(self):
        for explicit,env in ((True,{}),(False,{'NO_COLOR':'1'})):
            stream=Terminal()
            with patch.dict(os.environ,{'TERM':'xterm-256color',**env}):
                view=Presentation(stream,no_color=explicit)
                self.assertTrue(view.console.no_color)
                view.help('usage: example')
            sgr=re.findall(r'\x1b\[([0-9;]*)m',stream.getvalue())
            for code in sgr:
                parts=code.split(';')
                self.assertFalse(any(part in {'30','31','32','33','34','35','36','37','38','40','41','42','43','44','45','46','47','48','90','91','92','93','94','95','96','97'} for part in parts),code)

    def test_rich_and_plain_export_identical_candidate_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            results=[]
            for index,flags in enumerate(([],['--plain'],['--no-color'])):
                output=Path(folder)/str(index);terminal=Terminal();stdout=io.StringIO()
                with patch.dict(os.environ,{'TERM':'xterm-256color'}),redirect_stderr(terminal),redirect_stdout(stdout):
                    self.assertEqual(main(['generate','--seed',' café ','--seed','moon','--output',str(output),*flags]),0)
                self.assertEqual(stdout.getvalue(),'')
                results.append(output.read_bytes())
            self.assertEqual(results[0],results[1])
            self.assertEqual(results[1],results[2])

    def test_human_plan_uses_panel_but_plain_human_plan_avoids_ansi(self):
        for flags in ([],['--plain']):
            stream=Terminal()
            with patch.dict(os.environ,{'TERM':'xterm-256color'}),redirect_stdout(stream),redirect_stderr(io.StringIO()):
                self.assertEqual(main(['plan','--seed','word','--human',*flags]),0)
            self.assertIn('GENERATION PLAN',stream.getvalue())
            if flags:self.assertNotIn('\x1b',stream.getvalue())

    def test_markup_and_osc_in_rich_text_are_literal(self):
        stream=Terminal()
        with patch.dict(os.environ,{'TERM':'xterm-256color'}):
            Presentation(stream).text('[red]literal[/red]\x1b]52;c;secret\x07')
        self.assertNotIn('\x1b]52',stream.getvalue())
        self.assertIn('[red]literal[/red]',stream.getvalue())
