from pathlib import Path


def test_pyav_runtime_and_nixpacks_configuration():
    import av

    assert av.__version__ == "16.1.0"
    config = Path("nixpacks.toml").read_text()
    assert 'nixLibs = ["...", "gcc-unwrapped"]' in config
    assert 'PIP_ONLY_BINARY = "av"' in config
