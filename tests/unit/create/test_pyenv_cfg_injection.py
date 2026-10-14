"""Regression tests for configuration injection through ``pyvenv.cfg`` values (#3247).

pyvenv.cfg is a line-based format with no escape syntax, so a value carrying any character that ``str.splitlines``
treats as a boundary used to be read back as extra configuration lines. Since the last value for a key wins, a prompt
such as ``x"\\nhome = /attacker`` overrode ``home``. These tests mirror the examples of the hypothesis property tests in
``tests/property`` so the invariant is also exercised when hypothesis is not installed.

"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

import pytest
from python_discovery import PythonInfo

from virtualenv.create.pyenv_cfg import PyEnvCfg
from virtualenv.run import cli_run
from virtualenv.run.plugin.creators import CreatorSelector

if TYPE_CHECKING:
    from pathlib import Path

CURRENT_CREATORS = [
    i for i in CreatorSelector.for_interpreter(PythonInfo.current_system()).key_to_class if i != "builtin"
]

# Anything str.splitlines() treats as a boundary is what makes a value escape its own line.
LINE_BOUNDARIES = ("\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", chr(0x2028), chr(0x2029))


def _round_trip(content: dict[str, str], tmp_path: Path) -> dict[str, str]:
    path = tmp_path / "pyvenv.cfg"
    PyEnvCfg(OrderedDict(content), path).write()
    return dict(PyEnvCfg.from_file(path).content)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param({"prompt": 'x"\nhome = /attacker\nprompt = "z'}, id="prompt-newline-home"),
        pytest.param({"prompt": "a" + chr(0x2028) + "home = /attacker"}, id="prompt-line-separator-home"),
        pytest.param({"home": "a\r\nevil = 1"}, id="home-crlf-evil"),
        pytest.param({"home": "original", "prompt": 'x"\r\nhome = /attacker\r\nevil = 1\r\nprompt = "z'}, id="crlf"),
    ],
)
def test_writing_then_reading_never_invents_keys(content: dict[str, str], tmp_path: Path) -> None:
    read_back = _round_trip(content, tmp_path)

    assert set(read_back) <= set(content)


@pytest.mark.parametrize("boundary", LINE_BOUNDARIES, ids=lambda b: f"U+{ord(b[0]):04X}{'-LF' if len(b) > 1 else ''}")
def test_prompt_cannot_override_home(boundary: str, tmp_path: Path) -> None:
    content = OrderedDict([
        ("home", "original"),
        ("prompt", f"x{boundary}home = attacker{boundary}injected = yes"),
    ])

    read_back = _round_trip(content, tmp_path)

    assert set(read_back) == {"home", "prompt"}
    assert read_back["home"] == "original"
    assert "injected" not in read_back


@pytest.mark.parametrize("boundary", LINE_BOUNDARIES, ids=lambda b: f"U+{ord(b[0]):04X}{'-LF' if len(b) > 1 else ''}")
def test_key_cannot_inject_lines(boundary: str, tmp_path: Path) -> None:
    content = OrderedDict([("home", "original"), (f"evil{boundary}home", "attacker")])

    read_back = _round_trip(content, tmp_path)

    assert read_back["home"] == "original"
    assert len(read_back) == len(content)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param({"prompt": 'x"\nhome = /attacker\nprompt = "z'}, id="prompt-newline-home"),
        pytest.param({"a": "1" + "".join(LINE_BOUNDARIES) + "2", "b": "3"}, id="all-boundaries"),
    ],
)
def test_written_file_has_one_line_per_key(content: dict[str, str], tmp_path: Path) -> None:
    path = tmp_path / "pyvenv.cfg"
    PyEnvCfg(OrderedDict(content), path).write()

    lines = path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == len(content)
    assert all(" = " in line for line in lines)


def test_plain_values_round_trip_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # write() rewrites a value naming an existing path, which is not under test here
    content ={"implementation": "CPython", "version_info": "3.12.1.final.0", "prompt": "my env = (dev)", "x": "a b"}

    assert _round_trip(content, tmp_path) == content


@pytest.mark.parametrize("creator", CURRENT_CREATORS)
def test_cli_prompt_cannot_inject_pyvenv_cfg(tmp_path: Path, creator: str) -> None:
    attacker_home = str(tmp_path / "attacker")
    prompt = f'x"\nhome = {attacker_home}\r\ninjected = yes{chr(0x2028)}prompt = "z'
    cmd = [str(tmp_path / "env"), "--seeder", "app-data", "--without-pip", "--creator", creator, "--prompt", prompt]

    result = cli_run(cmd)

    cfg_path = result.creator.pyenv_cfg.path
    cfg = PyEnvCfg.from_file(cfg_path)
    assert "injected" not in cfg, list(cfg.content.keys())
    assert cfg["home"] != attacker_home
    lines = cfg_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(cfg.content)
    assert sum(1 for line in lines if line.split("=", 1)[0].strip() == "home") == 1


def test_env_prompt_cannot_inject_pyvenv_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    attacker_home = str(tmp_path / "attacker")
    monkeypatch.setenv("VIRTUALENV_PROMPT", f"x\nhome = {attacker_home}\ninjected = yes")

    result = cli_run([str(tmp_path / "env"), "--seeder", "app-data", "--without-pip"])

    cfg = PyEnvCfg.from_file(result.creator.pyenv_cfg.path)
    assert "injected" not in cfg, list(cfg.content.keys())
    assert cfg["home"] != attacker_home
