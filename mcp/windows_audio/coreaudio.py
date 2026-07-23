"""Core Audio (pycaw/comtypes) wrappers with graceful degradation. RAW acquisition talks COM;
the classify helpers (flow_of_id, flag_defaults) are pure and unit-tested. When pycaw/comtypes is
missing, every acquire function returns {"available": False, "error": ...} so the MCP still runs."""
import warnings

class AudioUnavailable(Exception):
    pass


def _pycaw():
    try:
        warnings.filterwarnings("ignore")
        import comtypes
        from comtypes import CLSCTX_ALL, cast, POINTER
        from pycaw.pycaw import (AudioUtilities, IAudioEndpointVolume, IMMDeviceEnumerator,
                                 EDataFlow, ERole)
        from pycaw.constants import CLSID_MMDeviceEnumerator
        return dict(comtypes=comtypes, CLSCTX_ALL=CLSCTX_ALL, cast=cast, POINTER=POINTER,
                    AudioUtilities=AudioUtilities, IAudioEndpointVolume=IAudioEndpointVolume,
                    IMMDeviceEnumerator=IMMDeviceEnumerator, EDataFlow=EDataFlow, ERole=ERole,
                    CLSID_MMDeviceEnumerator=CLSID_MMDeviceEnumerator)
    except Exception as e:
        raise AudioUnavailable("pycaw/comtypes not available: %s (pip install pycaw)" % e)


def flow_of_id(dev_id):
    s = str(dev_id or "")
    if s.startswith("{0.0.0"):
        return "render"
    if s.startswith("{0.0.1"):
        return "capture"
    return "unknown"


def _state_name(state):
    return str(state).rsplit(".", 1)[-1] if state is not None else "Unknown"


def list_endpoints():
    try:
        p = _pycaw()
    except AudioUnavailable as e:
        return {"available": False, "error": str(e), "endpoints": []}
    out = []
    for d in p["AudioUtilities"].GetAllDevices():
        try:
            out.append({"name": d.FriendlyName, "id": d.id, "flow": flow_of_id(d.id),
                        "state": _state_name(getattr(d, "state", None))})
        except Exception as e:
            out.append({"name": None, "error": str(e)})
    return {"available": True, "endpoints": out}


def _role_info(p, enum, flow_val, role_val):
    try:
        dev = enum.GetDefaultAudioEndpoint(flow_val, role_val)
    except Exception:
        return {"error": "no default"}   # 0x80070490 element-not-found when a role has no default
    adev = p["AudioUtilities"].CreateDevice(dev)
    info = {"name": adev.FriendlyName, "id": adev.id, "state": _state_name(getattr(adev, "state", None))}
    try:
        vol = p["cast"](dev.Activate(p["IAudioEndpointVolume"]._iid_, p["CLSCTX_ALL"], None),
                        p["POINTER"](p["IAudioEndpointVolume"]))
        info["volume"] = round(vol.GetMasterVolumeLevelScalar(), 3)
        info["mute"] = bool(vol.GetMute())
    except Exception:
        pass
    return info


def default_for_roles():
    try:
        p = _pycaw()
    except AudioUnavailable as e:
        return {"available": False, "error": str(e)}
    enum = p["comtypes"].CoCreateInstance(p["CLSID_MMDeviceEnumerator"], p["IMMDeviceEnumerator"],
                                          p["comtypes"].CLSCTX_INPROC_SERVER)
    roles = (("console", p["ERole"].eConsole), ("multimedia", p["ERole"].eMultimedia),
             ("communications", p["ERole"].eCommunications))
    res = {"available": True, "render": {}, "capture": {}}
    for flow_key, flow in (("render", p["EDataFlow"].eRender), ("capture", p["EDataFlow"].eCapture)):
        for rk, role in roles:
            res[flow_key][rk] = _role_info(p, enum, flow.value, role.value)
    return res


def list_sessions():
    try:
        p = _pycaw()
    except AudioUnavailable as e:
        return {"available": False, "error": str(e), "sessions": []}
    out = []
    for s in p["AudioUtilities"].GetAllSessions():
        try:
            name = s.Process.name() if s.Process else "system"
        except Exception:
            name = "unknown"
        out.append({"process": name, "state": int(s.State)})
    return {"available": True, "sessions": out}


def flag_defaults(default_render):
    """Pure: given the render role->info map, flag no-multimedia / no-communications output + reasons."""
    def bad(info):
        if not info or "error" in info:
            return "no default device set"
        if info.get("state") not in (None, "Active"):
            return "default device is %s (not Active)" % info.get("state")
        if info.get("mute") is True:
            return "default device is muted"
        if info.get("volume") == 0.0:
            return "default device volume is 0"
        return None
    reasons = {}
    for role in ("multimedia", "communications"):
        r = bad(default_render.get(role, {}))
        if r:
            reasons[role] = r
    return {"no_multimedia_output": "multimedia" in reasons,
            "no_communications_output": "communications" in reasons,
            "reasons": ["%s: %s" % (k, v) for k, v in reasons.items()]}
