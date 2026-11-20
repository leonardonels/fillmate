"""Command line interface. Run `fillmate` with no arguments to open the desktop app."""
import argparse
import sys
from pathlib import Path

from . import FillmateError, __version__, engine, mail, store
from .fill_docx import docx_to_pdf
from .mail.mailapp import open_draft
from .mail.senders import make_sender
from .pack import export_pack, import_pack, list_packs, load_pack
from .paths import examples_dir


def _find_pack(pack_id, settings):
    packs, _ = list_packs(store.packs_dir(settings))
    for p in packs:
        if p.id == pack_id:
            return p
    candidate = Path(pack_id)
    if (candidate / "pack.yaml").is_file():
        return load_pack(candidate)
    raise FillmateError(f"No installed pack '{pack_id}'. See `fillmate packs`.")


def _pairs(items, what):
    out = {}
    for item in items or []:
        if "=" not in item:
            raise FillmateError(f"{what} must look like key=value, got {item!r}.")
        key, value = item.split("=", 1)
        out[key.strip()] = value
    return out


def cmd_packs(args, settings):
    packs, problems = list_packs(store.packs_dir(settings))
    print(f"Packs folder: {store.packs_dir(settings)}")
    if not packs and not problems:
        print("No packs installed. Try: fillmate install-example")
    for p in packs:
        print(f"  {p.id:<24} {p.name} (v{p.version}, {len(p.documents)} documents)")
    for folder, error in problems:
        print(f"  ! {folder.name}: {error}")


def cmd_fields(args, settings):
    pack = _find_pack(args.pack, settings)
    profile = store.load_profile()
    print(f"{pack.name}\n\nProfile (fillmate profile --set id=value):")
    for f in pack.profile:
        mark = "*" if f.required else " "
        print(f" {mark}{f.id:<22} {f.label}  = {profile.get(f.id, '')!r}")
    print("\nFields (fillmate fill --set id=value):")
    for f in pack.fields:
        mark = "*" if f.required else " "
        extra = f" [{', '.join(f.options)}]" if f.options else ""
        default = f"  default: {f.default}" if f.default not in (None, "") else ""
        print(f" {mark}{f.id:<22} {f.type:<9} {f.label}{extra}{default}")
    presets = {**pack.presets, **store.load_user_presets(pack.id)}
    if presets:
        print(f"\nPresets (--preset): {', '.join(presets)}")


def cmd_profile(args, settings):
    profile = store.load_profile()
    if args.set:
        profile.update(_pairs(args.set, "--set"))
        store.save_profile(profile)
    for key, value in profile.items():
        print(f"{key}: {value}")


def cmd_fill(args, settings):
    pack = _find_pack(args.pack, settings)
    presets = {**pack.presets, **store.load_user_presets(pack.id)}
    preset = None
    if args.preset:
        if args.preset not in presets:
            raise FillmateError(f"Unknown preset '{args.preset}'. Available: {', '.join(presets) or 'none'}.")
        preset = presets[args.preset]
    ctx = engine.build_context(pack, store.load_profile(), _pairs(args.set, "--set"), preset)
    for w in ctx.warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    out = args.out or engine.output_folder(ctx, store.output_dir(settings))
    outputs = engine.generate(ctx, out)
    attachments = [o.path for o in outputs]
    if args.docx_pdf:
        attachments += [docx_to_pdf(o.path) for o in outputs if o.kind == "docx"]
    for path in attachments:
        print(f"created: {path}")
    if args.no_mail:
        return

    test_mode, allowed = settings["test_mode"], settings["test_recipients"]
    if test_mode:
        print("TEST MODE: recipients replaced by the test recipients.")
    d = engine.draft_email(ctx, test_mode, allowed)
    sender = make_sender(settings)
    msg = mail.build_message(sender.from_address() if sender else None,
                             d["to"], d["cc"], d["bcc"], d["subject"], d["body"], attachments)
    eml = mail.write_draft(msg, Path(out) / "email.eml")
    print(f"draft: {eml}  (to: {', '.join(d['to']) or '-'})")
    if args.open_draft:
        open_draft(eml, d, attachments)
    if args.send:
        if sender is None:
            raise FillmateError("Sending is set to 'mail app' in Settings: use --open-draft instead.")
        ok, why = sender.ready()
        if not ok:
            raise FillmateError(f"Cannot send: {why}. Set it up in the app's Settings → Email.")
        mail.send(sender, d["to"], d["cc"], d["bcc"], d["subject"], d["body"], attachments, test_mode, allowed)
        print(f"sent to: {', '.join(d['to'] + d['cc'] + d['bcc'])}")


def cmd_import(args, settings):
    pack = import_pack(args.path, store.packs_dir(settings), replace=args.replace)
    print(f"installed: {pack.id} ({pack.name}) in {pack.root}")


def cmd_export(args, settings):
    pack = _find_pack(args.pack, settings)
    print(f"exported: {export_pack(pack, args.dest)}")


def cmd_install_example(args, settings):
    for folder in sorted(examples_dir().iterdir()):
        if (folder / "pack.yaml").is_file():
            pack = import_pack(folder, store.packs_dir(settings), replace=True)
            print(f"installed: {pack.id}")


def build_parser():
    ap = argparse.ArgumentParser(prog="fillmate", description="Fill recurring paperwork from form packs.")
    ap.add_argument("--version", action="version", version=f"fillmate {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="open the desktop app (default)")
    sub.add_parser("packs", help="list installed packs")
    p = sub.add_parser("fields", help="show a pack's profile fields, fields and presets")
    p.add_argument("pack")
    p = sub.add_parser("profile", help="show or set profile values")
    p.add_argument("--set", action="append", metavar="ID=VALUE")
    p = sub.add_parser("fill", help="fill a pack's documents and optionally email them")
    p.add_argument("pack", help="pack id (or path to a pack folder)")
    p.add_argument("--preset")
    p.add_argument("--set", action="append", metavar="ID=VALUE",
                   help="field value; dates fields take a comma-separated list")
    p.add_argument("--out", type=Path, help="output folder")
    p.add_argument("--docx-pdf", action="store_true", help="also attach DOCX documents converted to PDF")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--no-mail", action="store_true", help="only create the documents")
    mode.add_argument("--open-draft", action="store_true", help="open the email in your mail app")
    mode.add_argument("--send", action="store_true", help="send with the sender configured in Settings")
    p = sub.add_parser("import", help="install a pack from a folder or .zip")
    p.add_argument("path", type=Path)
    p.add_argument("--replace", action="store_true", help="replace an installed pack with the same id")
    p = sub.add_parser("export", help="zip an installed pack to share it (no personal data)")
    p.add_argument("pack")
    p.add_argument("dest", type=Path)
    sub.add_parser("install-example", help="install the bundled example pack")
    return ap


COMMANDS = {"packs": cmd_packs, "fields": cmd_fields, "profile": cmd_profile, "fill": cmd_fill,
            "import": cmd_import, "export": cmd_export, "install-example": cmd_install_example}


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd in (None, "gui"):
        from .gui.app import main as gui_main
        return gui_main()
    try:
        COMMANDS[args.cmd](args, store.load_settings())
    except FillmateError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
