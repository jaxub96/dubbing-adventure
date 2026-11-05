"""Command line: gdub [ui] | extract | record | export | qc | actor | devices"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .project import Project


def _project(args) -> Project:
    return Project(Path(args.project))


def cmd_extract(args) -> int:
    from .extract import extract_paths
    from .lines import write_lines_csv
    res = extract_paths([Path(p) for p in args.sources], args.encoding)
    out = Path(args.output) if args.output else _project(args).lines_csv
    out.parent.mkdir(parents=True, exist_ok=True)
    write_lines_csv(res.lines, out)
    for w in res.warnings:
        print("warning:", w, file=sys.stderr)
    npcs = len({ln.npc for ln in res.lines})
    print(f"{len(res.lines)} lines, {npcs} character(s), from {res.files} file(s) -> {out}")
    return 0


def cmd_record(args) -> int:
    from . import audio
    from .session import Session
    from .workspace import Workspace
    project = _project(args)
    lines = project.load_lines()
    session = Session.load(project)
    adopted = session.reconcile(lines)
    if adopted:
        print(f"adopted {adopted} take file(s) found on disk that were not in session.json")
    ws = Workspace(lines, session)
    if args.voice:
        ws.set_filters(voice=args.voice)
    if args.npc:
        ws.set_filters(npc=next((n for n in ws.npcs() if args.npc.lower() in n.lower()), None))
    if args.simulate:
        engine = audio.SimulatedEngine(args.samplerate)
    else:
        dev = int(args.device) if args.device and args.device.isdigit() else args.device
        odev = int(args.output_device) if args.output_device and args.output_device.isdigit() \
            else args.output_device
        engine = audio.AudioEngine(args.samplerate, dev, args.channel, args.preroll, odev)
    from . import gui
    gui.run(ws, engine)
    return 0


def cmd_export(args) -> int:
    from .export import ExportSettings, export_all
    from .session import Session
    project = _project(args)
    lines, session = project.load_lines(), Session.load(project)
    cfg = ExportSettings(**{k: getattr(args, k) for k in ExportSettings.__dataclass_fields__})

    def show(i, n, row):
        if row.status == "error" or args.verbose:
            print(f"[{i}/{n}] {row.id}: {row.status} {row.detail}")

    res = export_all(project, lines, session, Path(args.out), cfg, args.voice, args.npc,
                     args.dry_run, show)
    msg = f"exported {res.count('ok')}, skipped {res.count('skipped')}, errors {res.count('error')}"
    lim = [r.id for r in res.rows if r.limited]
    if lim:
        msg += f"; {len(lim)} line(s) too quiet/peaky to reach the target level (limited by peak ceiling)"
    print(msg + ("" if args.dry_run else f" -> {args.out} (log: {project.export_log})"))
    return 1 if res.count("error") else 0


def cmd_qc(args) -> int:
    from .qc import QCSettings, format_report, run_qc
    from .session import Session
    project = _project(args)
    lines, session = project.load_lines(), Session.load(project)
    rep = run_qc(project, lines, session, Path(args.export) if args.export else None,
                 QCSettings(chars_per_second=args.cps))
    text = format_report(rep, args.limit)
    print(text)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    return 1 if args.strict and rep.issue_count else 0


def cmd_actor(args) -> int:
    from .session import Session
    project = _project(args)
    session = Session.load(project)
    a = session.set_actor(args.voice, name=args.name, mic=args.mic, distance=args.distance,
                          gain=args.gain, notes=args.notes)
    print(f"voice {args.voice}: {a}")
    return 0


def cmd_devices(args) -> int:
    from .audio import list_devices
    print(list_devices())
    return 0


def cmd_ui(args) -> int:
    from . import launcher
    launcher.run()
    return 0


def build_parser(export_defaults: dict | None = None) -> argparse.ArgumentParser:
    from .export import ExportSettings
    d = ExportSettings()
    p = argparse.ArgumentParser(prog="gdub", description=__doc__)
    sub = p.add_subparsers(dest="cmd")   # no command = open the launcher window

    def common(sp):
        sp.add_argument("-p", "--project", default=".", help="project folder (default: current)")
        return sp

    s = common(sub.add_parser("extract", help="build lines.csv from .d dialogue scripts"))
    s.add_argument("sources", nargs="+", help=".d files and/or folders (searched recursively)")
    s.add_argument("-o", "--output", help="output CSV (default: <project>/lines.csv)")
    s.add_argument("--encoding", default="auto",
                   help="script encoding: auto (utf-8, else cp1250), cp1250, cp1252, ...")
    s.set_defaults(func=cmd_extract)

    s = common(sub.add_parser("record", help="open the recorder"))
    s.add_argument("--voice", help="start filtered to a speaker voice, e.g. 15 (hero) or 09")
    s.add_argument("--npc", help="start filtered to a character (substring match)")
    s.add_argument("--device", help="input device name or index (see `gdub devices`)")
    s.add_argument("--output-device", help="playback device name or index")
    s.add_argument("--channel", type=int, default=1, help="input channel, 1-based (default 1)")
    s.add_argument("--samplerate", type=int, default=48000)
    s.add_argument("--preroll", type=int, default=300, help="pre-roll in ms (default 300)")
    s.add_argument("--simulate", action="store_true", help="no audio hardware (UI try-out)")
    s.set_defaults(func=cmd_record)

    s = common(sub.add_parser("export", help="process chosen takes into game-ready WAVs"))
    s.add_argument("--out", required=True, help="the game's speech folder")
    s.add_argument("--voice")
    s.add_argument("--npc")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true")
    s.add_argument("--sample-rate", dest="sample_rate", type=int, default=d.sample_rate)
    s.add_argument("--codec", default=d.codec, help="pcm_s16le (default), adpcm_ms, ...")
    s.add_argument("--channels", type=int, default=d.channels)
    s.add_argument("--highpass-hz", dest="highpass_hz", type=float, default=d.highpass_hz)
    s.add_argument("--trim-below-peak-db", dest="trim_below_peak_db", type=float, default=d.trim_below_peak_db)
    s.add_argument("--lead-ms", dest="lead_ms", type=float, default=d.lead_ms)
    s.add_argument("--tail-ms", dest="tail_ms", type=float, default=d.tail_ms)
    s.add_argument("--normalize", choices=["rms", "peak", "none"], default=d.normalize)
    s.add_argument("--target-db", dest="target_db", type=float, default=d.target_db,
                   help="speech RMS target in dBFS (default -20)")
    s.add_argument("--ceiling-db", dest="ceiling_db", type=float, default=d.ceiling_db)
    if export_defaults:
        s.set_defaults(**export_defaults)
    s.set_defaults(func=cmd_export)

    s = common(sub.add_parser("qc", help="quality-check report"))
    s.add_argument("--export", help="exported speech folder to check as well")
    s.add_argument("--report", help="also write the report to this file")
    s.add_argument("--limit", type=int, default=0, help="max entries per section (0 = all)")
    s.add_argument("--cps", type=float, default=13.0, help="expected characters per second")
    s.add_argument("--strict", action="store_true", help="exit 1 if anything is flagged")
    s.set_defaults(func=cmd_qc)

    s = common(sub.add_parser("actor", help="note mic/setup for a voice (stored in session.json)"))
    s.add_argument("voice", help="voice number, e.g. 15")
    s.add_argument("--name")
    s.add_argument("--mic")
    s.add_argument("--distance")
    s.add_argument("--gain")
    s.add_argument("--notes")
    s.set_defaults(func=cmd_actor)

    s = sub.add_parser("ui", help="open the launcher window (all commands as buttons)")
    s.set_defaults(func=cmd_ui)

    s = sub.add_parser("devices", help="list audio devices")
    s.set_defaults(func=cmd_devices)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # project.json can provide export defaults ({"export": {"sample_rate": 22050, ...}})
    defaults: dict = {}
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("-p", "--project", default=".")
    known, _ = pre.parse_known_args(argv)
    try:
        defaults = Project(Path(known.project)).config().get("export", {})
    except Exception as exc:
        print(f"warning: ignoring project.json: {exc}", file=sys.stderr)
    args = build_parser(defaults).parse_args(argv)
    try:
        return args.func(args) if hasattr(args, "func") else cmd_ui(args)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
