from __future__ import annotations

import os
import subprocess
import sys
from argparse import Namespace

import pytest

from virtualenv.activation import BatchActivator
from virtualenv.info import IS_WIN
from virtualenv.run import cli_run


@pytest.fixture(scope="session")
def special_char_name():
    """The shared tests/conftest.py fixture, minus &: cmd.exe cannot represent that one at all."""
    base = "'\";e-$ èрт🚒♞中片-j"
    if IS_WIN:
        base = base.replace('"', "").replace(";", "")
    encoding = "ascii" if IS_WIN else sys.getfilesystemencoding()
    result = ""
    for char in base:
        try:
            trip = char.encode(encoding, errors="strict").decode(encoding)
            if char == trip:
                result += char
        except ValueError:  # noqa: PERF203
            continue
    assert result
    return result


def test_batch_pydoc_bat_quoting(tmp_path) -> None:
    """Test that pydoc.bat properly quotes python.exe path to handle spaces."""

    # GIVEN: A mock interpreter
    class MockInterpreter:
        os = "nt"
        tcl_lib = None
        tk_lib = None

    class MockCreator:
        def __init__(self, dest) -> None:
            self.dest = dest
            self.bin_dir = dest / "Scripts"
            self.bin_dir.mkdir(parents=True)
            self.interpreter = MockInterpreter()
            self.pyenv_cfg = {}
            self.env_name = "test-env"

    creator = MockCreator(tmp_path)
    options = Namespace(prompt=None)
    activator = BatchActivator(options)

    # WHEN: Generate activation scripts
    activator.generate(creator)

    # THEN: pydoc.bat should quote python.exe to handle paths with spaces
    pydoc_content = (creator.bin_dir / "pydoc.bat").read_text(encoding="utf-8")

    # The python.exe should be quoted to handle paths with spaces like "C:\Program Files\Python39\python.exe"
    assert '"python.exe"' in pydoc_content, f"python.exe should be quoted in pydoc.bat. Content:\n{pydoc_content}"


@pytest.mark.parametrize(
    ("tcl_lib", "tk_lib", "present"),
    [
        ("C:\\tcl", "C:\\tk", True),
        (None, None, False),
    ],
)
def test_batch_tkinter_generation(tmp_path, tcl_lib, tk_lib, present) -> None:
    # GIVEN
    class MockInterpreter:
        os = "nt"

    interpreter = MockInterpreter()
    interpreter.tcl_lib = tcl_lib
    interpreter.tk_lib = tk_lib

    class MockCreator:
        def __init__(self, dest) -> None:
            self.dest = dest
            self.bin_dir = dest / "bin"
            self.bin_dir.mkdir()
            self.interpreter = interpreter
            self.pyenv_cfg = {}
            self.env_name = "my-env"

    creator = MockCreator(tmp_path)
    options = Namespace(prompt=None)
    activator = BatchActivator(options)

    # WHEN
    activator.generate(creator)
    activate_content = (creator.bin_dir / "activate.bat").read_text(encoding="utf-8")
    deactivate_content = (creator.bin_dir / "deactivate.bat").read_text(encoding="utf-8")

    # THEN
    # PKG_CONFIG_PATH is always set
    assert '@if defined PKG_CONFIG_PATH @set "_OLD_PKG_CONFIG_PATH=%PKG_CONFIG_PATH%"' in activate_content
    assert '@set "PKG_CONFIG_PATH=%VIRTUAL_ENV%\\lib\\pkgconfig;%PKG_CONFIG_PATH%"' in activate_content
    assert '@if defined _OLD_PKG_CONFIG_PATH @set "PKG_CONFIG_PATH=%_OLD_PKG_CONFIG_PATH%"' in deactivate_content
    assert "@if not defined _OLD_PKG_CONFIG_PATH @set PKG_CONFIG_PATH=" in deactivate_content
    assert "@set _OLD_PKG_CONFIG_PATH=" in deactivate_content

    if present:
        assert '@if NOT "C:\\tcl"=="" @set "TCL_LIBRARY=C:\\tcl"' in activate_content
        assert '@if NOT "C:\\tk"=="" @set "TK_LIBRARY=C:\\tk"' in activate_content
        assert "if defined _OLD_VIRTUAL_TCL_LIBRARY" in deactivate_content
        assert "if defined _OLD_VIRTUAL_TK_LIBRARY" in deactivate_content
    else:
        assert '@if NOT ""=="" @set "TCL_LIBRARY="' in activate_content
        assert '@if NOT ""=="" @set "TK_LIBRARY="' in activate_content


def test_batch_skips_destination_with_ampersand(tmp_path, caplog) -> None:
    """An `&` in the destination can never be represented, so generation must skip it, not guess."""

    class MockInterpreter:
        os = "nt"
        tcl_lib = None
        tk_lib = None

    class MockCreator:
        def __init__(self, dest) -> None:
            self.dest = dest
            self.bin_dir = dest / "Scripts"
            self.bin_dir.mkdir(parents=True)
            self.interpreter = MockInterpreter()
            self.pyenv_cfg = {}
            self.env_name = "test-env"

    creator = MockCreator(tmp_path / "weird&dest")
    activator = BatchActivator(Namespace(prompt=None))

    generated = activator.generate(creator)

    assert generated == []
    assert list(creator.bin_dir.iterdir()) == []
    assert "skipping batch activation scripts" in caplog.text
    assert "&" in caplog.text


class _MockInterpreter:
    os = "nt"

    def __init__(self, tcl_lib=None, tk_lib=None) -> None:
        self.tcl_lib = tcl_lib
        self.tk_lib = tk_lib


class _MockCreator:
    def __init__(self, dest, tcl_lib=None, tk_lib=None) -> None:
        self.dest = dest
        self.bin_dir = dest / "Scripts"
        self.bin_dir.mkdir(parents=True)
        self.interpreter = _MockInterpreter(tcl_lib, tk_lib)
        self.pyenv_cfg = {}
        self.env_name = "test-env"


def _generate(dest, prompt, **kwargs):
    creator = _MockCreator(dest, **kwargs)
    BatchActivator(Namespace(prompt=prompt)).generate(creator)
    return creator


# Each of these is a --prompt / VIRTUALENV_PROMPT / config-file value that, written verbatim into
# `@set "VIRTUAL_ENV_PROMPT=..."`, either closes the quoted value or starts a new cmd.exe statement.
_INJECTED_PROMPTS = [
    pytest.param('x" & type nul > pwned.marker & "', id="quote-breakout"),
    pytest.param('a"&type nul>pwned.marker&"b', id="quote-breakout-compact"),
    pytest.param('x" | type nul > pwned.marker | "', id="quote-breakout-pipe"),
    pytest.param("x & type nul > pwned.marker", id="bare-ampersand"),
    pytest.param("x^& type nul ^> pwned.marker ^& ", id="caret-escaped"),
    pytest.param("x\n@type nul > pwned.marker\n@rem ", id="newline"),
    pytest.param("x\r\n@type nul > pwned.marker\r\n@rem ", id="crlf"),
    pytest.param("x\r@type nul > pwned.marker\r@rem ", id="carriage-return"),
    pytest.param(f"x{chr(0x2028)}@type nul > pwned.marker", id="line-separator"),
]
_CMD_LIVE_CHARS = set('&|<>^!"')
_LINE_BOUNDARIES = set("\n\r\v\f\x1c\x1d\x1e\x85" + chr(0x2028) + chr(0x2029))
_PROMPT_SET = '@set "VIRTUAL_ENV_PROMPT='


@pytest.mark.parametrize("prompt", _INJECTED_PROMPTS)
def test_batch_prompt_injection_stays_inside_set_statement(tmp_path, prompt) -> None:
    clean = _generate(tmp_path / "clean", None)
    evil = _generate(tmp_path / "evil", prompt)
    clean_lines = (clean.bin_dir / "activate.bat").read_bytes().split(b"\n")
    evil_content = (evil.bin_dir / "activate.bat").read_bytes().decode("utf-8")

    # the payload must not add a single physical line (cmd.exe runs every line as its own statement)
    evil_lines = evil_content.split("\n")
    assert len(evil_lines) == len(clean_lines), evil_content
    assert not any(char in _LINE_BOUNDARIES for line in evil_lines for char in line.rstrip("\r")), evil_content

    # and it must stay inert data inside the single quoted `@set "VIRTUAL_ENV_PROMPT=..."` statement
    prompt_lines = [line.rstrip("\r") for line in evil_lines if line.startswith(_PROMPT_SET)]
    assert len(prompt_lines) == 1, evil_content
    (prompt_line,) = prompt_lines
    assert prompt_line.endswith('"'), prompt_line
    assert prompt_line.count('"') == 2, prompt_line
    value = prompt_line[len(_PROMPT_SET) : -1]
    assert not (set(value) & _CMD_LIVE_CHARS), prompt_line
    assert "pwned.marker" in value, prompt_line


def test_batch_prompt_variable_references_are_literal(tmp_path) -> None:
    creator = _generate(tmp_path, "%PATH%!PATH!(x86)")

    content = (creator.bin_dir / "activate.bat").read_text(encoding="utf-8")

    # `%%` is cmd.exe's in-file escape for a literal `%`; `!` has no escape inside quotes, so it is dropped
    assert '@set "VIRTUAL_ENV_PROMPT=%%PATH%% PATH (x86)"' in content, content


def test_batch_destination_with_parentheses_is_kept(tmp_path) -> None:
    creator = _generate(tmp_path / "Program Files (x86)" / "env", None)

    content = (creator.bin_dir / "activate.bat").read_text(encoding="utf-8")

    assert f'@set "VIRTUAL_ENV={creator.dest}"' in content, content


@pytest.mark.parametrize(
    ("tcl_lib", "tk_lib"),
    [
        pytest.param("C:\\tcl&calc", None, id="tcl"),
        pytest.param(None, "C:\\tk&calc", id="tk"),
    ],
)
def test_batch_skips_library_path_with_ampersand(tmp_path, caplog, tcl_lib, tk_lib) -> None:
    creator = _MockCreator(tmp_path / "env", tcl_lib, tk_lib)

    generated = BatchActivator(Namespace(prompt=None)).generate(creator)

    assert generated == []
    assert list(creator.bin_dir.iterdir()) == []
    assert "skipping batch activation scripts" in caplog.text
    assert "&calc" in caplog.text


def _run_activate_bat(activate_bat, cwd, *, delayed_expansion=False, env=None):
    """Run ``activate.bat`` through a real cmd.exe from ``cwd`` and record what VIRTUAL_ENV_PROMPT ended up as."""
    driver = cwd / "driver.bat"
    relative = os.path.relpath(str(activate_bat), str(cwd))
    driver.write_bytes(f'@call "{relative}"\r\n@set VIRTUAL_ENV_PROMPT> prompt.txt\r\n'.encode("ascii"))
    cmd = ["cmd.exe", "/d", *(["/v:on"] if delayed_expansion else []), "/c", "driver.bat"]
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, check=False, timeout=60, env=env)
    output = (result.stdout + result.stderr).decode(errors="replace")
    recorded_file = cwd / "prompt.txt"
    recorded = recorded_file.read_bytes().decode(errors="replace") if recorded_file.exists() else None
    return output, recorded


@pytest.mark.skipif(not IS_WIN, reason="exercises the generated activate.bat through a real cmd.exe")
@pytest.mark.parametrize("prompt", _INJECTED_PROMPTS)
def test_batch_prompt_injection_does_not_run_in_cmd(tmp_path, prompt) -> None:
    creator = _generate(tmp_path / "env", prompt)

    output, recorded = _run_activate_bat(creator.bin_dir / "activate.bat", tmp_path)

    assert not (tmp_path / "pwned.marker").exists(), output
    assert not (creator.bin_dir / "pwned.marker").exists(), output
    # activation itself still ran to completion and kept the payload as an inert prompt value
    assert recorded is not None, output
    assert recorded.startswith("VIRTUAL_ENV_PROMPT=x") or recorded.startswith("VIRTUAL_ENV_PROMPT=a"), recorded
    assert "pwned.marker" in recorded, recorded


@pytest.mark.skipif(not IS_WIN, reason="exercises the generated activate.bat through a real cmd.exe")
@pytest.mark.parametrize(
    ("prompt", "delayed_expansion"),
    [
        pytest.param("x%VENV_TEST_SECRET%y", False, id="percent-expansion"),
        pytest.param("x!VENV_TEST_SECRET!y", True, id="delayed-expansion"),
    ],
)
def test_batch_prompt_cannot_expand_variables_in_cmd(tmp_path, prompt, delayed_expansion) -> None:
    creator = _generate(tmp_path / "env", prompt)
    env = os.environ.copy()
    env["VENV_TEST_SECRET"] = "leaked-secret-value"

    output, recorded = _run_activate_bat(
        creator.bin_dir / "activate.bat", tmp_path, delayed_expansion=delayed_expansion, env=env
    )

    assert recorded is not None, output
    assert "leaked-secret-value" not in recorded, recorded
    assert recorded.startswith("VIRTUAL_ENV_PROMPT=x"), recorded
    assert "VENV_TEST_SECRET" in recorded, recorded


@pytest.mark.skipif(not IS_WIN, reason="exercises the generated activate.bat through a real cmd.exe")
@pytest.mark.parametrize("source", ["cli", "env"])
def test_batch_cli_prompt_injection_does_not_run_in_cmd(tmp_path, monkeypatch, source) -> None:
    payload = 'x" & type nul > pwned.marker & "'
    cmd = [str(tmp_path / "env"), "--without-pip", "--activators", "batch", "--no-periodic-update"]
    if source == "cli":
        cmd += ["--prompt", payload]
    else:
        monkeypatch.setenv("VIRTUALENV_PROMPT", payload)

    session = cli_run(cmd)
    activate_bat = session.creator.bin_dir / "activate.bat"
    assert activate_bat.exists()

    output, recorded = _run_activate_bat(activate_bat, tmp_path)

    assert not (tmp_path / "pwned.marker").exists(), output
    assert not (session.creator.bin_dir / "pwned.marker").exists(), output
    assert recorded is not None, output
    assert recorded.startswith("VIRTUAL_ENV_PROMPT=x"), recorded


def test_batch(activation_python, activation_tester_class, activation_tester, tmp_path) -> None:
    if not (activation_python.creator.bin_dir / "activate.bat").exists():
        pytest.skip("cmd.exe cannot represent this destination, batch activation was skipped on purpose")
    version_script = tmp_path / "version.bat"
    version_script.write_text("ver", encoding="utf-8")

    class Batch(activation_tester_class):
        def __init__(self, session) -> None:
            super().__init__(BatchActivator, session, None, "activate.bat", "bat")
            self._version_cmd = [str(version_script)]
            self._invoke_script = []
            self.deactivate = "call deactivate"
            self.activate_cmd = "call"
            self.pydoc_call = f"call {self.pydoc_call}"
            self.unix_line_ending = False

        def _get_test_lines(self, activate_script):
            return ["@echo off", *super()._get_test_lines(activate_script)]

        def quote(self, s):
            if '"' in s or " " in s:
                text = s.replace('"', r"\"")
                return f'"{text}"'
            return s

        def print_prompt(self) -> str:
            return 'echo "%PROMPT%"'

    activation_tester(Batch)


def test_batch_output(activation_python, activation_tester_class, activation_tester, tmp_path) -> None:
    if not (activation_python.creator.bin_dir / "activate.bat").exists():
        pytest.skip("cmd.exe cannot represent this destination, batch activation was skipped on purpose")
    version_script = tmp_path / "version.bat"
    version_script.write_text("ver", encoding="utf-8")

    class Batch(activation_tester_class):
        def __init__(self, session) -> None:
            super().__init__(BatchActivator, session, None, "activate.bat", "bat")
            self._version_cmd = [str(version_script)]
            self._invoke_script = []
            self.deactivate = "call deactivate"
            self.activate_cmd = "call"
            self.pydoc_call = f"call {self.pydoc_call}"
            self.unix_line_ending = False

        def _get_test_lines(self, activate_script):
            """Build an intermediary script that activates, echoes the current echo setting, then deactivates.

            This is what proves echo state survives activation and deactivation without leaking unwanted output.

            """
            intermediary_script_path = str(tmp_path / "intermediary.bat")
            activate_script_quoted = self.quote(str(activate_script))
            return [
                "@echo on",
                f"@echo @call {activate_script_quoted} > {intermediary_script_path}",
                f"@echo @echo >> {intermediary_script_path}",
                f"@echo @deactivate >> {intermediary_script_path}",
                f"@call {intermediary_script_path}",
            ]

        def assert_output(self, out, raw, tmp_path) -> None:  # noqa: ARG002
            assert out[0] == "ECHO is on.", raw

        def quote(self, s):
            if '"' in s or " " in s:
                text = s.replace('"', r"\"")
                return f'"{text}"'
            return s

        def print_prompt(self) -> str:
            return 'echo "%PROMPT%"'

    activation_tester(Batch)
