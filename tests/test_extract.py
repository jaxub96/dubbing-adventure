from gdub.extract import extract_text, extract_paths, scene_of, split_code_comment
from gdub.lines import read_lines_csv, write_lines_csv
from conftest import FIXTURES


def test_basic_line_and_ids():
    src = 'AI_Output (other, self, "DIA_VLK_001_Remus_INTRODUCTION_15_01"); //Who are you?\n'
    r = extract_text(src, "x.d", "VLK_001_Remus")
    (ln,) = r.lines
    assert (ln.id, ln.npc, ln.scene, ln.speaker, ln.order, ln.text, ln.who) == (
        "DIA_VLK_001_Remus_INTRODUCTION_15_01", "VLK_001_Remus", "INTRODUCTION", "15", "01",
        "Who are you?", "other")
    assert ln.npc_name == "Remus" and ln.is_hero


def test_npc_comes_from_instance_not_only_filename():
    src = 'instance DIA_X (C_INFO) {\n npc = VLK_002_Other;\n};\n' \
          'AI_Output (self, other, "DIA_X_Hi_10_00");//Hey, you there!\n'
    assert extract_text(src, "x.d", "fallback").lines[0].npc == "VLK_002_Other"


def test_comments_are_ignored():
    src = '''// AI_Output (self, other, "DIA_A_B_09_00");//line comment
/* AI_Output (self, other, "DIA_A_B_09_01");//in block
AI_Output (self, other, "DIA_A_B_09_02");//in block too
*/ AI_Output (self, other, "DIA_A_B_09_03");//real
/*//x
AI_Output (self, other, "DIA_A_B_09_04");//hidden
*///y
AI_Output (self, other, "DIA_A_B_09_05");//real2
'''
    assert [l.id for l in extract_text(src, "x.d", "A").lines] == ["DIA_A_B_09_03", "DIA_A_B_09_05"]


def test_slashes_inside_strings_and_flexible_whitespace():
    code, com, _ = split_code_comment('x = "http://a"; // c', False)
    assert code == 'x = "http://a"; ' and com == " c"
    src = 'AI_Output\t(self,other,"DIA_A_B_09_00");//T\nai_output (OTHER, SELF, "DIA_A_B_15_01") ;  //  spaced  \n'
    ls = extract_text(src, "x.d", "A").lines
    assert [l.text for l in ls] == ["T", "spaced"] and ls[1].who == "other"


def test_warnings_and_duplicates():
    src = ('AI_Output (self, other, "DIA_A_B_09_00");\n'
           'AI_Output (self, other, "BADID");//x\n'
           'AI_Output (self, other, "DIA_A_C_09_00");//one\n'
           'AI_Output (self, other, "DIA_A_C_09_00");//one\n'
           'AI_Output (self, other, "DIA_A_C_09_00");//two\n')
    r = extract_text(src, "x.d", "A")
    assert [l.id for l in r.lines] == ["DIA_A_B_09_00", "DIA_A_C_09_00"]
    joined = "\n".join(r.warnings)
    assert "no text" in joined and "BADID" in joined and "different text" in joined
    assert len(r.warnings) == 3


def test_scene_of():
    assert scene_of("DIA_Addon_Dexter_Hallo", "BDT_1060_Dexter") == "Hallo"
    assert scene_of("DIA_VLK_001_Remus_INTRODUCTION", "VLK_001_Remus") == "INTRODUCTION"
    assert scene_of("DIA_Other_Thing", "BDT_1060_Dexter") == "Other_Thing"


def test_real_dexter_script(tmp_path):
    r = extract_paths([FIXTURES / "DIA_BDT_1060_Dexter.d"])   # cp1250 file, auto-detected
    assert r.warnings == []
    assert len(r.lines) == 53        # 62 AI_Output occurrences - 9 inside /* */ blocks
    ids = [l.id for l in r.lines]
    assert "DIA_Dexter_Wo_09_01" not in ids and not any("Steckbrief" in l.text for l in r.lines)
    first = r.lines[0]
    assert first.id == "DIA_Dexter_Hallo_09_00" and first.npc == "BDT_1060_Dexter"
    assert first.text.startswith("Proszę, proszę") and "�" not in "".join(l.text for l in r.lines)
    assert {l.speaker for l in r.lines} == {"09", "15"}
    out = tmp_path / "lines.csv"
    write_lines_csv(r.lines, out)
    assert read_lines_csv(out) == r.lines          # CSV round trip, incl. Polish letters
