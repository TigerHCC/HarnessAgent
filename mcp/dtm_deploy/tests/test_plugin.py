import pytest

import plugin


def test_insert_test_plugin_copies_file(tmp_path):
    src = tmp_path / "src" / "Plugin.dll"
    src.parent.mkdir()
    src.write_bytes(b"binarycontent")
    dest_dir = tmp_path / "dest"

    result = plugin.insert_test_plugin(str(src), str(dest_dir))

    assert result["overwritten"] is False
    dest_file = dest_dir / "Plugin.dll"
    assert dest_file.read_bytes() == b"binarycontent"


def test_insert_test_plugin_rejects_non_dll(tmp_path):
    src = tmp_path / "notdll.txt"
    src.write_text("x")
    with pytest.raises(plugin.PluginError):
        plugin.insert_test_plugin(str(src), str(tmp_path / "dest"))


def test_insert_test_plugin_missing_file(tmp_path):
    with pytest.raises(plugin.PluginError):
        plugin.insert_test_plugin(str(tmp_path / "missing.dll"), str(tmp_path / "dest"))


def test_insert_test_plugin_requires_force_to_overwrite(tmp_path):
    src = tmp_path / "src" / "Plugin.dll"
    src.parent.mkdir()
    src.write_bytes(b"v1")
    dest_dir = tmp_path / "dest"
    plugin.insert_test_plugin(str(src), str(dest_dir))

    src.write_bytes(b"v2-longer")
    with pytest.raises(plugin.PluginError):
        plugin.insert_test_plugin(str(src), str(dest_dir))

    result = plugin.insert_test_plugin(str(src), str(dest_dir), force=True)
    assert result["overwritten"] is True
    assert (dest_dir / "Plugin.dll").read_bytes() == b"v2-longer"
