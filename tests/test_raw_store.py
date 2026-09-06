import json
import os

from seeing.capture import raw_store as rs


def test_roundtrip_and_verify(tmp_path):
    root = str(tmp_path / "cap")
    st = rs.RawStore(root, capturer_id="t1", software_version="test")
    body = json.dumps({"a": 1, "z": "ঢাকা"}).encode("utf-8")          # UTF-8 with Bengali
    st.write_data("src", key="GP", body=body, http={"status": 200, "url": "u"})
    st.write_data("src", key="GP", body=b"\xff\xfe\x00raw", http={"status": 200})   # non-UTF8 → b64
    st.write_gap("src", "http", detail="503", key="GP", http={"status": 503}, body=b"<html>err</html>")
    st.write_heartbeat({"x": 1})
    st.close()
    man = json.load(open(os.path.join(root, "MANIFEST.json")))
    assert {s["source"] for s in man["segments"]} == {"src", "heartbeat"}
    v = rs.verify_store(root)
    assert v["all_ok"], v
    # bodies come back byte-exact
    seg = [s for s in man["segments"] if s["source"] == "src"][0]
    recs = [r for r, ok in rs.iter_segment(os.path.join(root, seg["path"])) if ok]
    kinds = [r["kind"] for r in recs]
    assert kinds == ["META", "DATA", "DATA", "GAP", "TRAILER"]
    assert rs.decode_body(recs[1]) == body
    assert rs.decode_body(recs[2]) == b"\xff\xfe\x00raw"
    assert recs[2]["body_encoding"] == "b64"
    assert [r["seq"] for r in recs] == [0, 1, 2, 3, 4]


def test_compress_verify_and_chain(tmp_path):
    root = str(tmp_path / "cap")
    st = rs.RawStore(root, capturer_id="t1")
    for i in range(5):
        st.write_data("s", key=None, body=b"x" * 1000 + bytes([i]), http={})
    st.close()
    rep = st.compress_and_verify()
    assert rep["verified"] == rep["compressed"] >= 1 and not rep["failed"]
    man = json.load(open(os.path.join(root, "MANIFEST.json")))
    assert all(s.get("gz_path") for s in man["segments"])
    assert rs.verify_store(root)["all_ok"]
    # a second epoch continues the chain: META.prev_segment_sha256 == previous segment sha256
    st2 = rs.RawStore(root, capturer_id="t1")
    st2.write_data("s", key=None, body=b"y", http={})
    st2.close()
    man2 = json.load(open(os.path.join(root, "MANIFEST.json")))
    segs = [s for s in man2["segments"] if s["source"] == "s"]
    assert len(segs) == 2
    first = next(rs.iter_segment(os.path.join(root, segs[1]["path"])))[0]
    assert first["prev_segment_sha256"] == segs[0]["sha256"]
    assert rs.verify_store(root)["all_ok"]


def test_tampering_is_detected(tmp_path):
    root = str(tmp_path / "cap")
    st = rs.RawStore(root, capturer_id="t1")
    st.write_data("s", key=None, body=b"hello", http={})
    st.close()
    man = json.load(open(os.path.join(root, "MANIFEST.json")))
    p = os.path.join(root, man["segments"][0]["path"])
    lines = open(p, "rb").read().split(b"\n")
    lines[1] = lines[1].replace(b"hello", b"hellp")
    open(p, "wb").write(b"\n".join(lines))
    v = rs.verify_segment(p)
    assert not v["ok"] and (v["bad_records"] > 0 or not v["trailer_matches"])
