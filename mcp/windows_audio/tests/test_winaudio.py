import winaudio as wa

def test_classify_bt():
    assert wa.classify_bt("FIIO BTR15 Hands-Free") == "hfp"
    assert wa.classify_bt("LE_WH-H900N (h.ear) Hands-Free") == "hfp"
    assert wa.classify_bt("Pixel 6 Pro A2DP SNK") == "a2dp"
    assert wa.classify_bt("Bose Mini SoundLink") == "a2dp"

def test_summarize_mic_privacy_denied():
    s = wa.summarize_mic_privacy("Deny", {"Teams": "Allow", "SomeApp": "Deny"})
    assert s["global"] == "Deny"
    assert "SomeApp" in s["denied_apps"] and "Teams" not in s["denied_apps"]

def test_summarize_mic_privacy_allow():
    s = wa.summarize_mic_privacy("Allow", {"Teams": "Allow"})
    assert s["global"] == "Allow" and s["denied_apps"] == []
