"""Importação de patches TFI (TFM Music Maker)."""
import pytest

from mdconv.fm.patch import Operator, Patch
from mdconv.library.importer import import_folder
from mdconv.library.patch_library import PatchLibrary
from mdconv.library.tfi import TfiError, carrier_order_score, parse_tfi, to_tfi


def sample_patch():
    return Patch(alg=4, fb=5, ops=[
        Operator(mul=2, dt=-3, tl=35, ks=1, ar=31, d1r=8, d2r=2, sl=4, rr=7),
        Operator(mul=1, dt=0, tl=5, ks=0, ar=30, d1r=4, d2r=1, sl=2, rr=6),
        Operator(mul=3, dt=2, tl=40, ks=2, ar=29, d1r=9, d2r=3, sl=5, rr=8),
        Operator(mul=1, dt=3, tl=8, ks=3, ar=31, d1r=3, d2r=0, sl=1, rr=9)])


def test_layout_matches_spec():
    b = to_tfi(sample_patch())
    assert len(b) == 42 and b[0] == 4 and b[1] == 5
    # arquivo na ordem dos registradores: S1, S3, S2, S4
    # S1: MUL, DT(−3→0), TL, RS, AR, DR, SR, RR, SL, SSG
    assert list(b[2:12]) == [2, 0, 35, 1, 31, 8, 2, 7, 4, 0]
    assert list(b[12:15]) == [3, 5, 40]      # 2ª posição = S3 (MUL 3, DT +2, TL 40)
    assert list(b[22:25]) == [1, 3, 5]       # 3ª posição = S2 (MUL 1, DT 0, TL 5)
    assert b[32 + 1] == 6                    # S4 DT +3 → 6


def test_roundtrip():
    p = sample_patch()
    q = parse_tfi(to_tfi(p)).patch
    assert q.alg == p.alg and q.fb == p.fb
    assert [vars(o) for o in q.ops] == [vars(o) for o in p.ops]


def test_errors_and_warnings():
    with pytest.raises(TfiError):
        parse_tfi(b"\x00" * 41)
    b = bytearray(to_tfi(sample_patch()))
    b[11] = 9                         # SSG-EG ligado no op1
    b[3] = 7                          # DT fora de 0–6
    info = parse_tfi(bytes(b))
    assert info.uses_ssg_eg
    assert any("SSG-EG" in w for w in info.warnings) and any("DT=7" in w for w in info.warnings)


def test_carrier_order_score():
    good = parse_tfi(to_tfi(sample_patch()))
    swapped = sample_patch()
    swapped.ops[1], swapped.ops[2] = swapped.ops[2], swapped.ops[1]   # como se fosse 1,3,2,4
    bad = parse_tfi(to_tfi(swapped))
    assert carrier_order_score([good])[:2] == (1.0, 0.0)
    assert carrier_order_score([bad])[:2] == (0.0, 1.0)


def test_import_folder(tmp_path):
    src = tmp_path / "tfi" / "baixos"
    src.mkdir(parents=True)
    (src / "Baixo1.tfi").write_bytes(to_tfi(sample_patch()))
    (src / "Copia.tfi").write_bytes(to_tfi(sample_patch()))
    (src / "ruim.tfi").write_bytes(b"\x01\x02")
    rep = import_folder(tmp_path / "tfi", tmp_path / "lib", tag="pack")
    assert len(rep.imported) == 1 and len(rep.skipped) == 1 and len(rep.errors) == 1
    e = PatchLibrary(tmp_path / "lib").entries()[0]
    assert not e.approved and "baixos" in e.tags and "pack" in e.tags
    assert len(e.profile) > 5 and e.centroid > 0


def test_bank_finds_itself_and_octave(tmp_path):
    import copy

    from mdconv.library.bank import PatchBank, build_bank, patch_descriptor
    src = tmp_path / "tfi"
    src.mkdir()
    base = sample_patch()
    other = Patch(alg=7, fb=0, ops=[Operator(mul=1, tl=0, ar=31, d1r=0, sl=0, rr=15)] +
                  [Operator(tl=127) for _ in range(3)])
    high = copy.deepcopy(base)
    for o in high.ops:
        o.mul *= 2                                        # mesmo som, uma oitava acima
    (src / "base.tfi").write_bytes(to_tfi(base))
    (src / "seno.tfi").write_bytes(to_tfi(other))
    s = build_bank(src, tmp_path / "bank")
    assert s["in_bank"] == 2
    bank = PatchBank(tmp_path / "bank")
    prof, env = patch_descriptor(base)
    m = bank.nearest(prof, env, k=2)
    assert m[0].name == "base" and m[0].octave == 0
    prof_h, env_h = patch_descriptor(high)
    # o som de "high" é o de "base" uma oitava acima: o banco acha "base" com oitava +1
    m = bank.nearest(prof_h, env_h, k=1)
    assert m[0].name == "base" and m[0].octave == 1
