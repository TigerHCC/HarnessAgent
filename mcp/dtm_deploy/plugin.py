"""Pure-Python reimplementation of Insert-TestPlugin.ps1 -- copies a test plugin DLL into the DTP
TransmissionPlugins directory.
"""
import os
import shutil


class PluginError(Exception):
    pass


def insert_test_plugin(plugin_path, dest_dir, force=False):
    if not os.path.isfile(plugin_path):
        raise PluginError("plugin file not found: %s" % plugin_path)
    if os.path.splitext(plugin_path)[1].lower() != ".dll":
        raise PluginError("invalid file extension (expected .dll): %s" % plugin_path)

    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, os.path.basename(plugin_path))
    already_exists = os.path.isfile(dest_path)
    if already_exists and not force:
        raise PluginError("plugin already exists at %s (pass force=true to overwrite)" % dest_path)

    shutil.copy2(plugin_path, dest_path)

    src_size = os.path.getsize(plugin_path)
    dest_size = os.path.getsize(dest_path)
    if src_size != dest_size:
        raise PluginError("file size mismatch after copy (source=%d, dest=%d)" % (src_size, dest_size))

    return {"plugin_path": plugin_path, "destination_path": dest_path, "overwritten": already_exists}
