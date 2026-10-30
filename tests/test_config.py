from livesub import config


def test_roundtrip(tmp_path):
    s = config.Settings()
    s.display.mode = "zh"
    s.display.font_size = 50
    s.audio.device = "麥克風 (Realtek)"
    p = tmp_path / "設定" / "config.toml"
    config.save(s, p)
    assert config.load(p) == s


def test_missing_file_gives_defaults(tmp_path):
    assert config.load(tmp_path / "nope.toml") == config.Settings()


def test_corrupt_file_gives_defaults(tmp_path):
    p = tmp_path / "c.toml"
    p.write_bytes(b"\xff\xfe not toml [[[")
    assert config.load(p) == config.Settings()


def test_values_are_clamped_and_validated():
    s = config.from_dict({
        "display": {"mode": "klingon", "position": "left", "style": 3, "font_size": 9999, "box_opacity": -2,
                    "max_lines": "lots", "width_pct": float("nan"), "screen": -5, "clear_after_s": True},
        "audio": {"source_language": "fr", "device": 123},
        "runtime": {"translator": "google", "hotkeys": "yes"},
        "unknown": {"x": 1},
    })
    d = s.display
    assert (d.mode, d.position, d.style) == ("bilingual", "bottom", "box")
    assert d.font_size == config.FONT_MAX and d.box_opacity == 0.0 and d.max_lines == 2
    assert d.width_pct == 80 and d.screen == 0 and d.clear_after_s == 6.0
    assert s.audio.source_language == "auto" and s.audio.device == ""
    assert s.runtime.translator == "ct2" and s.runtime.hotkeys is True


def test_save_is_atomic(tmp_path):
    p = tmp_path / "config.toml"
    config.save(config.Settings(), p)
    assert p.exists() and not p.with_suffix(".tmp").exists()
