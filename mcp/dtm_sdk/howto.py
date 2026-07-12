"""Extract sections from Sample_Utilities_HowTo.md so the agent can read a command's real options
instead of us hard-coding 65 signatures.
"""
import re

UTIL_HEADINGS = {
    "dtmutil": "DTMUtil",
    "instrumentation": "DtpInstrumentationUtil",
    "analytics": "DtpAnalyticsUtil",
    "transmission": "DtpTransmissionUtil",
    "platinum": "DTMPlatinumUtil",
}


def util_section(text, util):
    heading = UTIL_HEADINGS.get(util)
    if not heading:
        return ""
    m = re.search(r"^## %s\b.*?$" % re.escape(heading), text, re.M)
    if not m:
        return ""
    start = m.start()
    nxt = re.search(r"^## ", text[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    return text[start:end].strip()


def command_help(text, util, command):
    section = util_section(text, util)
    if not section:
        return ""
    m = re.search(r"^#### `%s`.*?$" % re.escape(command), section, re.M)
    if not m:
        return section
    start = m.start()
    nxt = re.search(r"^(#### |### )", section[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(section)
    return section[start:end].strip()
