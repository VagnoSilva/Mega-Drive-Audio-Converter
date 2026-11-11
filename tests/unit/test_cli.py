import json

from mdconv.cli import main
from tests.fixtures.builders import gen, midi_file, off, on, pc, sf2_file, track_bytes
from mdconv.io_sf2.sf2 import GEN_ID as G
import numpy as np


def test_cli_analyze_writes_text_and_json(tmp_path, capsys):
    mid = tmp_path / "m.mid"
    mid.write_bytes(midi_file([track_bytes([(0, pc(0, 1)), (0, on(0, 60, 100)), (480, off(0, 60))])]))
    sf = tmp_path / "b.sf2"
    sf.write_bytes(sf2_file(
        [{"name": "S", "data": np.zeros(100, np.int16)}],
        [{"name": "I", "zones": [[gen(G["sampleID"], 0)]]}],
        [{"name": "P", "bank": 0, "program": 1, "zones": [[gen(G["instrument"], 0)]]}]))
    out = tmp_path / "r.json"
    assert main(["analyze", str(mid), str(sf), "--json", str(out)]) == 0
    text = capsys.readouterr().out
    assert "'P'" in text and "notas" in text
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["midi"]["note_count"] == 1 and data["matches"][0]["preset_name"] == "P"
