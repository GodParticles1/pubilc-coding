#!/usr/bin/env python3
"""Green V1.1 image integrity and bounded file transaction.

The shell service helper owns all process control. This module never controls a
service itself, resets a database, creates accounts, or restores a running rootfs.
Python 3.8+ is an explicit deployment prerequisite, including on ARM targets.
"""
import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import pathlib
import platform
import posixpath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile

SCHEMA = "green-migration-image-v1.1"
META = "image-v11.json"
META_SHA = "image-v11.sha256"
IDENTITY_KEYS = ("arch", "os_id", "os_version", "product", "version")
BROAD = {"opt", "etc", "var", "bin", "sbin", "lib", "lib64", "usr"}
PROTECTED = (
    "/.green-v11-restore", "/.green-v11-target.json", "/.green-v11-fixture",
    "/boot", "/dev", "/proc", "/sys", "/run", "/tmp", "/var/run",
    "/etc/mtab", "/etc/fstab", "/etc/hostname", "/etc/hosts",
    "/etc/resolv.conf", "/etc/machine-id", "/etc/localtime",
    "/etc/localtime_bak", "/etc/local.conf", "/var/lib/dbus/machine-id",
    "/etc/ssh", "/etc/network", "/etc/netplan", "/etc/sysconfig/network",
    "/etc/sysconfig/network-scripts", "/opt/data/.xmig_stage",
    "/opt/data/.xmig_backup", "/opt/data/green_migration_relay",
    "/opt/data/green_migration_auto", "/var/log", "/opt/log", "/var/tmp",
    "*.log", "*.pid", "*.sock", "*.swp", "*.tmp",
)


class Blocked(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Blocked(message)


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical(obj):
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def fsync_dir(path):
    if os.name == "nt":
        return  # Windows fixtures do not claim Linux durability acceptance.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_bytes(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".green-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as dst:
            dst.write(data)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp, path)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path, obj):
    atomic_bytes(path, canonical(obj) + b"\n")


def read_json(path):
    with open(path, encoding="utf-8") as src:
        return json.load(src)


def safe_relative(value):
    require(isinstance(value, str) and value and "\\" not in value and
            not any(ord(c) < 32 for c in value), "invalid relative path")
    require(not value.startswith("/") and not re.match(r"^[A-Za-z]:", value), "absolute path rejected")
    parts = value.split("/")
    require(all(p not in ("", ".", "..") for p in parts), "path traversal/noncanonical path rejected")
    return value


def managed_path(value):
    require(isinstance(value, str) and value.startswith("/") and value != "/", "managed path must be absolute and non-root")
    return safe_relative(value[1:])


def contains(root, child):
    return child == root or child.startswith(root + "/")


def no_symlink_ancestors(path, include_leaf=False):
    path = pathlib.Path(path).absolute()
    parts = [path] + list(path.parents)
    for p in parts if include_leaf else parts[1:]:
        require(not p.is_symlink(), "symlink ancestor rejected: " + str(p))
    return path


def safe_link(name, target, roots):
    require(target and "\\" not in target and not any(ord(c) < 32 for c in target), "invalid symlink target")
    if target.startswith("/"):
        resolved = posixpath.normpath(target).lstrip("/")
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
    require(resolved not in (".", "..") and not resolved.startswith("../"), "symlink escapes image root")
    safe_relative(resolved)
    require(any(contains(r, resolved) for r in roots), "symlink target outside manifest-owned trees")


def parse_manifest(path):
    require(path.is_file() and not path.is_symlink(), "missing/unsafe manifest.txt")
    text = path.read_text(encoding="utf-8")
    require(text.count("\nitems:\n") == 1 or text.startswith("items:\n"), "invalid manifest items section")
    items = text.split("items:\n", 1)[1].splitlines()
    require(items and all(x.strip() == x and x for x in items), "empty/invalid item list")
    roots = [managed_path(x) for x in items]
    require(len(set(roots)) == len(roots), "duplicate manifest item")
    for a in roots:
        require(not any(a != b and contains(b, a) for b in roots), "overlapping manifest items")
        require(not excluded(a, PROTECTED), "forbidden manifest item")
    return roots


def read_excludes(path):
    patterns = []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        require("\\" not in line and ".." not in line.split("/") and
                not any(ord(c) < 32 for c in line), "invalid exclusion")
        patterns.append(line.rstrip("/"))
    return list(dict.fromkeys(list(PROTECTED) + patterns))


def excluded(rel, patterns):
    pieces = rel.split("/")
    ancestors = ["/".join(pieces[:i]) for i in range(1, len(pieces) + 1)]
    for pattern in patterns:
        if pattern.startswith("/"):
            pattern = pattern[1:]
            if any(fnmatch.fnmatchcase(x, pattern) for x in ancestors):
                return True
        elif "/" in pattern:
            if any(fnmatch.fnmatchcase(x, pattern) for x in ancestors):
                return True
        elif any(fnmatch.fnmatchcase(x, pattern) for x in pieces):
            return True
    return False


def assert_supported_metadata(path):
    """Fail closed where the current adapter cannot preserve extra metadata.

    Windows is a fixture host only. It does not prove Linux metadata capability.
    On Linux the API must positively report no ACL/xattr/capability attributes;
    an adapter that preserves and verifies them is required otherwise.
    """
    system = platform.system()
    if system == "Windows":
        return
    require(system == "Linux", "unsupported metadata platform; verified metadata adapter required")
    inspect = getattr(os, "listxattr", None)
    require(callable(inspect), "Linux xattr inventory unavailable; verified metadata adapter required")
    try:
        attrs = inspect(path, follow_symlinks=False)
    except (OSError, NotImplementedError, TypeError) as exc:
        raise Blocked("Linux xattr inventory failed; verified metadata adapter required: " + str(exc))
    require(isinstance(attrs, list) and not attrs,
            "ACL/xattr/capability present or unrecognized; verified metadata adapter required")


def source_preflight(source_root, items_file, exclude_file):
    """Read-only source metadata gate; never read/hash live database contents."""
    root = no_symlink_ancestors(source_root, True)
    require(root.is_dir(), "source root missing")
    roots = []
    for line in pathlib.Path(items_file).read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            require(line == line.strip(), "noncanonical source item")
            roots.append(managed_path(line))
    require(roots and len(roots) == len(set(roots)), "empty/duplicate source items")
    require(not any(a != b and contains(a, b) for a in roots for b in roots), "overlapping source items")
    patterns = read_excludes(exclude_file)
    require(not any(excluded(p, patterns) for p in roots), "protected/excluded source item")
    checked = 0

    def visit(rel):
        nonlocal checked
        safe_relative(rel)
        if excluded(rel, patterns):
            return
        path = root.joinpath(*rel.split("/"))
        no_symlink_ancestors(path)
        st = path.lstat()
        require(stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode),
                "special source file requires reviewed adapter")
        assert_supported_metadata(path)
        checked += 1
        if stat.S_ISLNK(st.st_mode):
            safe_link(rel, os.readlink(path), roots)
        elif stat.S_ISDIR(st.st_mode):
            for name in sorted(os.listdir(path)):
                visit(rel + "/" + name)

    for rel in roots:
        path = root.joinpath(*rel.split("/"))
        no_symlink_ancestors(path)
        require(os.path.lexists(path), "source item missing: " + rel)
        visit(rel)
    return checked


def inventory(root, roots, patterns=(), require_roots=False):
    """lstat-only walker: never visit symlink referents; record hardlink groups."""
    root = pathlib.Path(root)
    require(root.is_dir() and not root.is_symlink(), "missing/unsafe files root")
    result, seen_inodes = {}, {}

    def visit(rel):
        safe_relative(rel)
        if patterns and excluded(rel, patterns):
            return
        path = root.joinpath(*rel.split("/"))
        no_symlink_ancestors(path)
        st = path.lstat()
        assert_supported_metadata(path)
        entry = {"mode": stat.S_IMODE(st.st_mode), "uid": st.st_uid,
                 "gid": st.st_gid, "mtime_ns": st.st_mtime_ns}
        if stat.S_ISDIR(st.st_mode):
            entry["kind"] = "dir"
            result[rel] = entry
            for name in sorted(os.listdir(path)):
                visit(rel + "/" + name)
        elif stat.S_ISREG(st.st_mode):
            entry.update(kind="file", size=st.st_size, sha256=digest(path))
            after = path.lstat()
            require((st.st_size, st.st_mtime_ns, st.st_ino) ==
                    (after.st_size, after.st_mtime_ns, after.st_ino), "file changed while hashing")
            key = (st.st_dev, st.st_ino)
            if st.st_nlink > 1 and key in seen_inodes:
                entry["hardlink_to"] = seen_inodes[key]
            else:
                seen_inodes[key] = rel
            result[rel] = entry
        elif stat.S_ISLNK(st.st_mode):
            target = os.readlink(path)
            safe_link(rel, target, roots)
            entry.update(kind="symlink", target=target)
            result[rel] = entry
        else:
            raise Blocked("special file rejected: " + rel)

    for rel in roots:
        p = root.joinpath(*rel.split("/"))
        no_symlink_ancestors(p)
        if not os.path.lexists(p):
            require(not require_roots, "missing manifest-owned tree: " + rel)
            continue
        visit(rel)
    return result


def comparable(entries):
    # Nanosecond times are restored, but archive filesystem precision is not an
    # integrity assertion. Contents/type/owner/mode/hardlink relationships are.
    normalized = normalize_hardlinks(entries)
    return {p: {k: v for k, v in e.items() if k != "mtime_ns"} for p, e in normalized.items()}


def normalize_hardlinks(entries):
    result = {p: dict(e) for p, e in entries.items()}
    groups = {}
    for p, e in entries.items():
        if e["kind"] == "file":
            groups.setdefault(e.get("hardlink_to", p), []).append(p)
    for paths in groups.values():
        paths.sort()
        first = paths[0]
        for p in paths:
            result[p].pop("hardlink_to", None)
            if p != first:
                result[p]["hardlink_to"] = first
    return result


def preserve_excluded_containers(root, roots, desired, current, patterns):
    """Retain directories needed to contain an excluded child; no dereference."""
    result, protected = dict(desired), []
    for base in roots:
        start = pathlib.Path(root).joinpath(*base.split("/"))
        if not start.is_dir() or start.is_symlink():
            continue
        for parent, dirs, files in os.walk(start, followlinks=False):
            for name in list(dirs) + files:
                path = pathlib.Path(parent) / name
                rel = path.relative_to(root).as_posix()
                if excluded(rel, patterns):
                    protected.append(rel)
                    if name in dirs:
                        dirs.remove(name)
                    ancestor = posixpath.dirname(rel)
                    while ancestor and contains(base, ancestor):
                        if ancestor in result:
                            require(result[ancestor]["kind"] == "dir", "image parent would replace excluded descendants")
                        elif ancestor in current:
                            require(current[ancestor]["kind"] == "dir", "protected descendant parent is unsafe")
                            result[ancestor] = current[ancestor]
                        ancestor = posixpath.dirname(ancestor)
    return result, sorted(protected)


def metadata_files(image):
    return {n: digest(image / n) for n in ("manifest.txt", "owners.tsv", "exclude.txt")}


def validate_entry(rel, entry, roots=None):
    safe_relative(rel)
    require(isinstance(entry, dict), "invalid inventory entry")
    kind = entry.get("kind")
    require(kind in ("dir", "file", "symlink"), "invalid inventory kind")
    base = {"kind", "mode", "uid", "gid", "mtime_ns"}
    allowed = base | ({"size", "sha256", "hardlink_to"} if kind == "file" else
                      {"target"} if kind == "symlink" else set())
    required = allowed - {"hardlink_to"}
    require(required <= set(entry) <= allowed, "invalid inventory entry schema")
    for name in ("mode", "uid", "gid", "mtime_ns"):
        require(type(entry[name]) is int, "invalid numeric inventory metadata")
    require(0 <= entry["mode"] <= 0o7777 and
            0 <= entry["uid"] < 2**32 - 1 and 0 <= entry["gid"] < 2**32 - 1 and
            -(2**63) <= entry["mtime_ns"] < 2**63, "inventory metadata out of range")
    if roots is not None:
        require(any(contains(r, rel) for r in roots), "inventory path outside manifest-owned trees")
    if kind == "file":
        require(type(entry["size"]) is int and entry["size"] >= 0 and
                isinstance(entry["sha256"], str) and
                re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is not None, "invalid file inventory metadata")
        if "hardlink_to" in entry:
            safe_relative(entry["hardlink_to"])
    elif kind == "symlink":
        require(isinstance(entry["target"], str), "invalid symlink inventory target")
        if roots is not None:
            safe_link(rel, entry["target"], roots)


def load_contract(image):
    """Parse/check ALL metadata without applying any of it to the filesystem."""
    image = no_symlink_ancestors(image, True)
    for name in (META, META_SHA, "manifest.txt", "owners.tsv", "exclude.txt"):
        require((image / name).is_file() and not (image / name).is_symlink(), "missing/unsafe image metadata: " + name)
    expected = (image / META_SHA).read_text().strip()
    require(re.fullmatch(r"[0-9a-f]{64}", expected) is not None and expected == digest(image / META), "metadata checksum mismatch")
    data = read_json(image / META)
    require(isinstance(data, dict) and data.get("schema") == SCHEMA, "unsupported image schema")
    roots = parse_manifest(image / "manifest.txt")
    require(roots == data.get("items"), "manifest inventory contract mismatch")
    validate_owners(image, roots)
    require(data.get("metadata") == metadata_files(image), "manifest/owner/exclusion checksum mismatch")
    require(read_excludes(image / "exclude.txt") == data.get("excludes"), "exclusion contract mismatch")
    require(isinstance(data.get("identity"), dict) and
            all(isinstance(data["identity"].get(k), str) and data["identity"][k]
                for k in ("source_id",) + IDENTITY_KEYS), "invalid image identity schema")
    entries = data.get("entries")
    require(isinstance(entries, dict) and entries, "empty/invalid inventory")
    for rel, entry in entries.items():
        validate_entry(rel, entry, roots)
    require(all(r in entries for r in roots), "manifest-owned root absent from inventory")
    for rel, entry in entries.items():
        parent = posixpath.dirname(rel)
        while parent and any(contains(r, parent) for r in roots):
            require(parent in entries and entries[parent]["kind"] == "dir", "inventory parent is not a declared directory")
            parent = posixpath.dirname(parent)
        if "hardlink_to" in entry:
            other = entries.get(entry["hardlink_to"])
            require(other is not None and other["kind"] == "file" and
                    "hardlink_to" not in other and entry["hardlink_to"] != rel and
                    all(other[k] == entry[k] for k in ("sha256", "size", "mode", "uid", "gid", "mtime_ns")),
                    "invalid hardlink inventory relation")
    services = data.get("desired_services")
    require(services is None or (isinstance(services, list) and
            all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", x) for x in services) and
            len(services) == len(set(services))),
            "invalid desired service inventory")
    return data


def content_comparable(entries):
    return {p: {k: v for k, v in e.items() if k not in ("mode", "uid", "gid", "mtime_ns")}
            for p, e in normalize_hardlinks(entries).items()}


def verify_payload(image, data, check_metadata=True):
    roots = data["items"]
    actual = inventory(image / "files", roots, require_roots=True)
    view = comparable if check_metadata else content_comparable
    require(view(actual) == view(data["entries"]), "image payload inventory/checksum mismatch")
    verify_owner_completeness(image, roots, data["excludes"], actual)
    all_entries = inventory(image / "files", sorted(os.listdir(image / "files")))
    require(all(any(contains(r, p) or contains(p, r) for r in roots) for p in all_entries), "unexpected payload outside manifest")
    return actual


def validate_owners(image, roots):
    path = image / "owners.tsv"
    require(path.is_file() and not path.is_symlink(), "missing/unsafe owners.tsv")
    seen = set()
    for row in path.read_text(encoding="utf-8").splitlines():
        fields = row.split("\t")
        require(len(fields) == 5, "invalid owner manifest row")
        raw = fields[0]
        rel = safe_relative(raw[2:] if raw.startswith("./") else raw.lstrip("/"))
        require(any(contains(r, rel) for r in roots), "owner path outside managed trees")
        require(rel not in seen and fields[3].isdigit() and fields[4].isdigit(), "invalid/duplicate owner identity")
        seen.add(rel)
    return seen


def verify_owner_completeness(image, roots, patterns, entries):
    source_names = {p for p in validate_owners(image, roots) if not excluded(p, patterns)}
    payload_names = {p for p in entries if not excluded(p, patterns)}
    require(source_names == payload_names, "source owner inventory and payload completeness mismatch")


def seal(image, identity, exclude_file, desired_services=None):
    image = no_symlink_ancestors(image, True)
    require(image.is_dir(), "image directory missing")
    if (image / META).exists():
        data = verify(image)
        require(data["identity"] == identity, "sealed image identity cannot change")
        require(digest(exclude_file) == data["metadata"]["exclude.txt"], "sealed exclusion contract cannot change")
        return data
    roots = parse_manifest(image / "manifest.txt")
    validate_owners(image, roots)
    require(all(isinstance(identity.get(k), str) and identity[k] for k in ("source_id",) + IDENTITY_KEYS), "incomplete image identity")
    excludes = read_excludes(exclude_file)
    exclude_bytes = pathlib.Path(exclude_file).read_bytes()
    atomic_bytes(image / "exclude.txt", exclude_bytes)
    entries = inventory(image / "files", roots, require_roots=True)
    verify_owner_completeness(image, roots, excludes, entries)
    # Reject unexpected top-level payloads, including accidental source mounts.
    all_entries = inventory(image / "files", sorted(os.listdir(image / "files")))
    allowed = lambda p: any(contains(r, p) or contains(p, r) for r in roots)
    require(all(allowed(p) for p in all_entries), "payload outside manifest-owned trees")
    services = None
    if desired_services:
        services = pathlib.Path(desired_services).read_text().splitlines()
        require(len(set(services)) == len(services) and all(re.fullmatch(r"[A-Za-z0-9_.-]+", x) for x in services), "invalid desired service set")
    data = {"schema": SCHEMA, "identity": identity, "items": roots,
            "excludes": excludes, "metadata": metadata_files(image), "entries": entries,
            "desired_services": services}
    write_json(image / META, data)
    atomic_bytes(image / META_SHA, (digest(image / META) + "\n").encode())
    # Every file and its directory entry must be durable before the cold copy
    # can be considered sealed. Failure retains .inprogress.
    sync_tree(image)
    verify(image, allow_inprogress=True)
    if (image / ".inprogress").exists():
        (image / ".inprogress").unlink()
    fsync_dir(image)
    return data


def sync_tree(root):
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in files:
            p = pathlib.Path(parent) / name
            if not p.is_symlink():
                sync_file(p)
        fsync_dir(parent)


def sync_file(path):
    if os.name == "nt":
        return  # Fixtures only; production restore is Linux-only.
    no_symlink_ancestors(path, True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), "durability target is not a regular file")
        os.fsync(fd)
    finally:
        os.close(fd)


def verify(image, allow_inprogress=False):
    image = no_symlink_ancestors(image, True)
    require(image.is_dir(), "image directory missing")
    require(allow_inprogress or not (image / ".inprogress").exists(), "unfinished image (.inprogress)")
    allowed_top = {META, META_SHA, "manifest.txt", "owners.tsv", "exclude.txt", "files"}
    if allow_inprogress:
        allowed_top.add(".inprogress")
    require(set(os.listdir(image)) <= allowed_top, "unexpected/incomplete/invalid image marker or metadata")
    data = load_contract(image)
    verify_payload(image, data)
    return data


def read_checksum(path):
    text = pathlib.Path(str(path) + ".sha256").read_text(encoding="ascii").strip()
    value = text.split()[0] if text else ""
    require(re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None, "invalid archive checksum sidecar")
    require(digest(path) == value.lower(), "archive checksum mismatch")


def unpack(archive, dest):
    archive, dest = pathlib.Path(archive), no_symlink_ancestors(dest, True)
    require(not os.path.lexists(dest), "unpack destination already exists")
    read_checksum(archive)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = pathlib.Path(tempfile.mkdtemp(prefix=".green-unpack-", dir=dest.parent))
    try:
        with tarfile.open(archive, "r:*") as tar:
            members = tar.getmembers()
            by_name = {}
            total = 0
            for m in members:
                name = safe_relative(m.name.rstrip("/"))
                require(name not in by_name, "duplicate tar member")
                require(m.isdir() or m.isfile() or m.issym() or m.islnk(), "special tar member rejected")
                by_name[name] = m
                total += m.size
            require(shutil.disk_usage(dest.parent).free >= total + 1024 * 1024, "insufficient unpack space")
            for name, m in by_name.items():
                for parent in pathlib.PurePosixPath(name).parents:
                    if str(parent) != "." and str(parent) in by_name:
                        require(by_name[str(parent)].isdir(), "tar path traverses non-directory/symlink")
                if m.issym():
                    require("files/" in name, "metadata symlink rejected")
                    payload = name.split("files/", 1)[1]
                    payload_roots = sorted({n.split("files/", 1)[1].split("/", 1)[0]
                                            for n in by_name if "files/" in n and n.split("files/", 1)[1]})
                    safe_link(payload, m.linkname, payload_roots)
                if m.islnk():
                    link = safe_relative(m.linkname)
                    require(link in by_name and by_name[link].isfile(), "invalid tar hardlink target")
            for name, m in sorted(by_name.items(), key=lambda pair: (len(pair[0].split("/")), pair[0])):
                p = temp.joinpath(*name.split("/"))
                no_symlink_ancestors(p)
                p.parent.mkdir(parents=True, exist_ok=True)
                if m.isdir():
                    p.mkdir(exist_ok=True)
                elif m.isfile():
                    with tar.extractfile(m) as src, open(p, "xb") as dst:
                        shutil.copyfileobj(src, dst)
                elif m.issym():
                    os.symlink(m.linkname, p)
                # Hardlinks are created after all regular members.
            for name, m in by_name.items():
                if m.islnk():
                    p = temp.joinpath(*name.split("/"))
                    p.parent.mkdir(parents=True, exist_ok=True)
                    os.link(temp.joinpath(*m.linkname.split("/")), p)
            candidates = [p.parent for p in temp.rglob("manifest.txt") if (p.parent / META).is_file()]
            require(len(candidates) == 1, "archive must contain one sealed image")
            image = candidates[0]
            data = load_contract(image)
            # Tar/sidecar checksums do not confer trust. Validate declared paths,
            # every actual lstat kind/link relation and all content hashes BEFORE
            # applying permissions, owners or timestamps from metadata.
            verify_payload(image, data, check_metadata=False)
            restore_metadata(image / "files", data["entries"])
            verify(image)
            sync_tree(image)
            os.replace(image, dest)
            fsync_dir(dest.parent)
        return dest
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def archive_image(image, output, prefix=""):
    image, output = no_symlink_ancestors(image, True).resolve(), no_symlink_ancestors(output, True)
    require(image not in output.parents, "archive must be outside its source image")
    with transaction_lock(output.parent / ("." + output.name + ".green-archive")):
        _archive_image_locked(image, output, prefix)


def _archive_image_locked(image, output, prefix=""):
    image, output = pathlib.Path(image), no_symlink_ancestors(output, True)
    verify(image)
    require(not output.exists() and not pathlib.Path(str(output) + ".sha256").exists(), "archive already exists")
    if prefix:
        safe_relative(prefix)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".green-archive-", dir=output.parent)
    os.close(fd)
    tmp = pathlib.Path(tmp_name)
    try:
        mode = "w:gz" if str(output).endswith((".tar.gz", ".tgz")) else "w"
        with tarfile.open(tmp, mode, format=tarfile.PAX_FORMAT, dereference=False) as tar:
            for child in sorted(image.iterdir()):
                if child.name != ".inprogress":
                    tar.add(child, arcname=posixpath.join(prefix, child.name), recursive=True)
        if os.name != "nt":
            with open(tmp, "rb") as src:
                os.fsync(src.fileno())
        atomic_bytes(str(tmp) + ".sha256", (digest(tmp) + "\n").encode())
        check = pathlib.Path(tempfile.mkdtemp(prefix=".green-archive-check-", dir=output.parent))
        try:
            unpack(tmp, check / "image")
        finally:
            shutil.rmtree(check)
        # Publish checksum first: until the archive rename no consumer sees a
        # complete pair; afterwards both refer to the verified exact bytes.
        os.replace(str(tmp) + ".sha256", str(output) + ".sha256")
        os.replace(tmp, output)
        fsync_dir(output.parent)
    finally:
        for p in (tmp, pathlib.Path(str(tmp) + ".sha256")):
            if p.exists():
                p.unlink()


def restore_metadata(root, entries):
    root = no_symlink_ancestors(root, True)
    checked = []
    # Validate the entire batch first, including actual leaf kinds; no preceding
    # valid-looking entry may cause a write before a later attack is rejected.
    for rel, entry in entries.items():
        validate_entry(rel, entry)
        path = root.joinpath(*rel.split("/"))
        no_symlink_ancestors(path, include_leaf=entry["kind"] != "symlink")
        st = path.lstat()
        kind = "dir" if stat.S_ISDIR(st.st_mode) else "file" if stat.S_ISREG(st.st_mode) else "symlink" if stat.S_ISLNK(st.st_mode) else "special"
        require(kind == entry["kind"], "actual filesystem kind differs from inventory")
        if kind == "symlink":
            require(os.readlink(path) == entry["target"], "actual symlink target differs from inventory")
        checked.append((rel, entry, path, st))
    for rel, entry, path, st in sorted(checked, key=lambda item: (-item[0].count("/"), item[0])):
        if entry["kind"] == "symlink":
            if os.name != "nt":
                os.chown(path, entry["uid"], entry["gid"], follow_symlinks=False)
            if os.utime in os.supports_follow_symlinks:
                os.utime(path, ns=(entry["mtime_ns"], entry["mtime_ns"]), follow_symlinks=False)
        elif os.name != "nt":
            # fchmod/fchown/utime operate on an O_NOFOLLOW descriptor. Neither a
            # false entry kind nor a replaced leaf can redirect metadata writes.
            require(hasattr(os, "O_NOFOLLOW"), "nofollow metadata capability required")
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
            try:
                actual = os.fstat(fd)
                require((actual.st_dev, actual.st_ino, stat.S_IFMT(actual.st_mode)) ==
                        (st.st_dev, st.st_ino, stat.S_IFMT(st.st_mode)), "metadata leaf changed during validation")
                apply_fd_metadata(fd, entry)
            finally:
                os.close(fd)
        else:
            # Windows is fixture-only for restore; production target modes are
            # refused by linux_root_process. The verified leaf is never a link.
            os.chmod(path, entry["mode"])
            os.utime(path, ns=(entry["mtime_ns"], entry["mtime_ns"]))


def apply_fd_metadata(fd, entry):
    # Linux chown can clear setuid/setgid. Apply the declared mode afterwards.
    os.fchown(fd, entry["uid"], entry["gid"])
    os.fchmod(fd, entry["mode"])
    os.utime(fd, ns=(entry["mtime_ns"], entry["mtime_ns"]))


def copy_entries(source, dest, entries, preserve_existing=False):
    source, dest = pathlib.Path(source), pathlib.Path(dest)
    entries = normalize_hardlinks(entries)
    written = {}
    for rel, entry in sorted(entries.items(), key=lambda item: (bool(item[1].get("hardlink_to")), item[0].count("/"), item[0])):
        src, dst = source.joinpath(*rel.split("/")), dest.joinpath(*rel.split("/"))
        no_symlink_ancestors(src)
        no_symlink_ancestors(dst)
        existed = os.path.lexists(dst)
        if preserve_existing and existed:
            continue
        if entry["kind"] == "dir":
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if entry.get("hardlink_to"):
                target = dest.joinpath(*entry["hardlink_to"].split("/"))
                require(target.is_file() and not target.is_symlink(), "hardlink referent not available")
                os.link(target, dst)
            elif entry["kind"] == "symlink":
                os.symlink(entry["target"], dst)
            else:
                shutil.copy2(src, dst, follow_symlinks=False)
        written[rel] = entry
    restore_metadata(dest, written)


def linux_root_process():
    return platform.system() == "Linux" and os.name != "nt" and os.geteuid() == 0


def target_profile(args):
    root = no_symlink_ancestors(args.target_root, True).resolve()
    require(root.is_dir(), "unsafe target root")
    if args.target_mode == "fixture":
        require(root != pathlib.Path(root.anchor), "fixture cannot target running root")
        path = root / ".green-v11-fixture"
    else:
        path = pathlib.Path(args.target_profile) if args.target_profile else root / ".green-v11-target.json"
    require(path.is_file() and not path.is_symlink(), "reviewed target profile missing")
    data = read_json(path)
    require(data.get("target_id") == args.target_id, "wrong target identity")
    if args.target_mode != "fixture":
        require(linux_root_process(), "root Linux recovery/target process required")
        require((root / "etc/machine-id").read_text().strip() == args.target_id, "target machine-id mismatch")
        require(platform.machine() == args.arch, "recovery/target architecture mismatch")
        os_release = {}
        for line in (root / "etc/os-release").read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os_release[key] = value.strip().strip('"').strip("'")
        require(os_release.get("ID") == args.os_id and os_release.get("VERSION_ID") == args.os_version,
                "target root OS facts mismatch")
        if args.target_mode == "offline":
            require(root != pathlib.Path("/").resolve() and os.path.ismount(root), "offline recovery root must be an independent mount")
            require(not args.service_cycle, "offline root services must start after boot, not on recovery host")
        else:
            require(root == pathlib.Path("/").resolve(), "managed host root must be running /")
            require(args.service_cycle, "live managed target requires service lifecycle")
    return root, data


def nested_mounts(root, roots, mountinfo=None):
    path = pathlib.Path(mountinfo or "/proc/self/mountinfo")
    if not path.is_file():
        require(os.name == "nt", "mount inventory unavailable")
        return []
    result = []
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) < 5:
            raise Blocked("invalid mount inventory")
        mount = fields[4].replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")
        for rel in roots:
            managed = str(root.joinpath(*rel.split("/")))
            if mount == managed or mount.startswith(managed.rstrip("/") + "/"):
                result.append(mount)
    return result


def validate_args(args, data, recovery=False):
    require(re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", args.token) is not None, "invalid transaction token")
    require(1 <= args.service_timeout <= 3600, "invalid service timeout")
    require(args.source_id.lower() not in ("unknown", "unverified", "unavailable") and
            args.target_id.lower() not in ("unknown", "unverified", "unavailable"), "unknown source/target identity")
    root, profile = target_profile(args)
    require(not args.use_image_services or (args.service_cycle and data.get("desired_services") is not None),
            "image desired-state contract requires a service cycle and recorded service set")
    for key in IDENTITY_KEYS:
        expected = getattr(args, key)
        require(expected and expected.lower() not in ("unknown", "unverified", "unavailable"), "unknown target compatibility fact: " + key)
        require(data["identity"].get(key) == expected == profile.get(key), "target/image compatibility mismatch: " + key)
    require(data["identity"].get("source_id") == args.source_id, "wrong image source identity")
    if args.restore_mode == "replace-managed":
        require(args.source_id != args.target_id, "same-node destructive replacement rejected")
    roots = []
    for line in pathlib.Path(args.allowlist).read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            roots.append(managed_path(line))
    require(roots and len(roots) == len(set(roots)), "empty/duplicate allowlist")
    require(not any(a != b and contains(a, b) for a in roots for b in roots), "overlapping allowlist")
    for rel in roots:
        require(any(contains(r, rel) for r in data["items"]), "allowlist outside manifest-owned trees")
        require(not excluded(rel, data["excludes"]), "protected allowlist rejected")
        require(rel in data["entries"], "allowlisted image tree missing")
        require(data["entries"][rel]["kind"] in ("dir", "symlink", "file"), "unsupported managed root")
        if args.target_mode == "managed":
            require(data["entries"][rel]["kind"] == "dir" and
                    not root.joinpath(*rel.split("/")).is_symlink(), "live managed-root must be a real directory")
        if args.target_mode == "managed":
            require(rel.startswith("opt/") and rel.count("/") >= 1, "live replacement is restricted to narrow /opt subtrees")
        if args.restore_mode == "replace-managed" and rel in BROAD:
            require(args.target_mode in ("offline", "fixture"), "broad root image requires offline recovery mount")
    if args.target_mode != "fixture":
        require(not nested_mounts(root, roots), "nested/independent target mounts need a separate recovery contract")
    for external in (pathlib.Path(args.image).resolve(), pathlib.Path(args.state_dir).resolve()):
        for rel in roots:
            managed = root.joinpath(*rel.split("/"))
            require(external != managed and managed not in external.parents, "image/state inside managed target tree")
    state = no_symlink_ancestors(args.state_dir, True)
    desired = {p: e for p, e in data["entries"].items()
               if any(contains(r, p) for r in roots) and not excluded(p, data["excludes"])}
    current = inventory(root, roots, data["excludes"])
    desired, protected = preserve_excluded_containers(root, roots, desired, current, data["excludes"])
    # Every ancestor used for a write must be a real directory. Reject
    # symlink/file collisions before stopping services or creating a snapshot.
    for p, e in desired.items():
        parent = posixpath.dirname(p)
        while parent:
            if parent in current and current[parent]["kind"] != "dir":
                require(args.restore_mode == "replace-managed" and parent in desired and desired[parent]["kind"] == "dir", "target path ancestor collision")
            parent = posixpath.dirname(parent)
    needed = sum(e.get("size", 0) for e in desired.values()) * 2 + sum(e.get("size", 0) for e in current.values()) + 1024 * 1024
    probe = state
    while not probe.exists():
        probe = probe.parent
    if not recovery:
        require(shutil.disk_usage(probe).free >= needed, "insufficient staging/snapshot disk space")
        require(shutil.disk_usage(root).free >= sum(e.get("size", 0) for e in desired.values()) + 1024 * 1024, "insufficient target disk space")
    plan = change_plan(desired, current, args.restore_mode, data["excludes"], protected)
    return root, roots, desired, current, plan


def change_plan(desired, current, mode, patterns, protected):
    changed, deleted, preserved = [], [], []
    for p in sorted(desired):
        if p in current and mode == "merge-preserve":
            preserved.append(p)
        elif p not in current or comparable({p: desired[p]}) != comparable({p: current[p]}):
            changed.append(p)
    if mode == "replace-managed":
        deleted = sorted(set(current) - set(desired))
    plan = {"changed": changed, "deleted": deleted, "preserved": preserved,
            "mode": mode, "exact_mirror": mode == "replace-managed",
            "protected_exclusions": list(patterns), "excluded_existing": protected}
    return plan


def service(args, op, desired=None):
    if not args.service_cycle:
        require(args.target_mode in ("offline", "fixture"), "service lifecycle required for live target")
        return
    require(args.services_helper, "services helper required")
    command = ["bash", args.services_helper, op, args.token]
    if op == "target-freeze":
        command.append("--yes")
    if desired:
        command.extend(["--desired-file", str(desired)])
    result = subprocess.run(command, check=False, timeout=args.service_timeout)
    require(result.returncode == 0, "service helper failed: " + op)


def remove_entries(root, current, desired):
    root = pathlib.Path(root)
    for rel, old in sorted(current.items(), key=lambda item: (-item[0].count("/"), item[0]), reverse=False):
        if rel in desired and desired[rel]["kind"] == old["kind"] == "dir":
            continue
        path = root.joinpath(*rel.split("/"))
        no_symlink_ancestors(path)
        if not os.path.lexists(path):
            continue
        if path.is_dir() and not path.is_symlink():
            try:
                path.rmdir()
            except OSError:
                # An excluded descendant may be retained only if the image
                # still expects a directory here; never replace its parent.
                require(rel in desired and desired[rel]["kind"] == "dir", "protected/nonempty directory cannot be replaced")
        else:
            path.unlink()


def reconcile(source, root, old, desired, mode):
    if mode == "replace-managed":
        remove_entries(root, old, desired)
    copy_entries(source, root, desired, preserve_existing=mode == "merge-preserve")


@contextlib.contextmanager
def transaction_lock(state):
    state = no_symlink_ancestors(state, True)
    state.mkdir(parents=True, exist_ok=True)
    lock = state / ".lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise Blocked("transaction lock exists; inspect owner.json, do not blindly clear")
    try:
        write_json(lock / "owner.json", {"pid": os.getpid(), "host": platform.node()})
        yield
    finally:
        (lock / "owner.json").unlink()
        lock.rmdir()


def contract(args, data, roots):
    return {"token": args.token, "root": str(pathlib.Path(args.target_root).resolve()),
            "image_sha256": hashlib.sha256(canonical(data) + b"\n").hexdigest(), "source_id": args.source_id,
            "target_id": args.target_id, "mode": args.restore_mode, "roots": roots,
            "state_dir": str(pathlib.Path(args.state_dir).resolve()),
            "service_cycle": args.service_cycle, "target_mode": args.target_mode,
            "desired_services": data.get("desired_services") if args.use_image_services else None}


def apply_image(args, recover=False):
    state = pathlib.Path(args.state_dir)
    if recover:
        require(re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", args.token) is not None, "invalid recovery token")
        stored = no_symlink_ancestors(state / "sessions" / args.token / "image-contract.json", True)
        data = read_json(stored)
        active_record = read_json(no_symlink_ancestors(state / "active.json", True))
        require(digest(stored) == active_record["contract"]["image_sha256"], "saved image contract corrupted")
    else:
        data = verify(pathlib.Path(args.image))
    root, roots, desired, current, plan = validate_args(args, data, recovery=recover)
    if not recover and args.restore_mode == "merge-preserve":
        print("RESTORE_MODE=merge-preserve; NOT_EXACT_MIRROR=TRUE")
    expected = contract(args, data, roots)
    with transaction_lock(root / ".green-v11-restore"), transaction_lock(state):
        active = state / "active.json"
        root_active = root / ".green-v11-restore" / "active.json"
        session = state / "sessions" / args.token
        record_file = session / "session.json"
        if record_file.exists() and not active.exists():
            record = read_json(record_file)
            require(record["contract"] == expected, "completed token belongs to another contract")
            require(record["phase"] == "COMPLETED", "incomplete session requires explicit recover")
            require(not recover, "already completed; no recovery action")
            print("TARGET_ALREADY_COMPLETED=" + args.token)
            return
        if active.exists():
            record = read_json(active)
            require(recover and record["contract"] == expected, "active recovery token exists; use exact-token recover")
            require(record_file.exists(), "recovery journal missing")
            require(root_active.is_file() and read_json(root_active)["contract"] == expected,
                    "target recovery ownership differs from state journal")
        else:
            require(not root_active.exists(), "another state directory owns the target recovery token")
            require(not recover, "no active recovery transaction")
            session.mkdir(parents=True, exist_ok=False)
            write_json(session / "image-contract.json", data)
            record = {"contract": expected, "phase": "PREFLIGHT_OK", "changed": False,
                      "freeze_requested": False, "services_started": False}
            write_json(session / "initial-plan.json", plan)
            write_json(session / "plan.json", plan)

        def save(phase):
            record["phase"] = phase
            write_json(record_file, record)
            write_json(active, record)
            write_json(root_active, record)

        def rollback():
            if record["changed"]:
                service(args, "target-requiesce")
                snap = session / "snapshot"
                before = read_json(session / "snapshot.json")
                require(digest(session / "snapshot.json") == record.get("snapshot_sha256"), "rollback snapshot inventory checksum mismatch")
                require(comparable(inventory(snap, roots, data["excludes"])) == comparable(before), "rollback snapshot corrupted; recovery retained")
                now = inventory(root, roots, data["excludes"])
                reconcile(snap, root, now, before, "replace-managed")
                require(comparable(inventory(root, roots, data["excludes"])) == comparable(before), "rollback verification failed")
                sync_tree(root_for_sync(root, roots, session))
            if record["freeze_requested"]:
                service(args, "target-resume")
                service(args, "target-complete")
            save("ROLLED_BACK")
            active.unlink()
            root_active.unlink()
            fsync_dir(state)
            print("TARGET_ROLLED_BACK=" + args.token)

        if recover:
            rollback()
            return
        save("PREFLIGHT_OK")
        try:
            stage = session / "stage"
            stage.mkdir()
            copy_entries(pathlib.Path(args.image) / "files", stage, desired)
            require(comparable(inventory(stage, roots, data["excludes"], True)) == comparable(desired), "stage verification failed")
            sync_tree(stage)
            save("STAGED_VERIFIED")
            record["freeze_requested"] = args.service_cycle
            save("FREEZE_REQUESTED")
            service(args, "target-freeze")
            service(args, "target-check")
            # Target may have legitimately written during preflight/staging.
            # Capture the exact quiescent preimage and final deletion preview.
            current = inventory(root, roots, data["excludes"])
            desired, protected = preserve_excluded_containers(root, roots, desired, current, data["excludes"])
            final_plan = change_plan(desired, current, args.restore_mode, data["excludes"], protected)
            write_json(session / "plan.json", final_plan)
            record["plan_sha256"] = digest(session / "plan.json")
            if final_plan != plan:
                print("FINAL_PLAN_UPDATED=" + args.token + "; SCOPE=AUTHORIZED_ALLOWLIST; INITIAL_AND_FINAL_RETAINED")
            snap = session / "snapshot"
            snap.mkdir()
            copy_entries(root, snap, current)
            write_json(session / "snapshot.json", current)
            record["snapshot_sha256"] = digest(session / "snapshot.json")
            require(comparable(inventory(snap, roots, data["excludes"])) == comparable(current), "snapshot verification failed")
            sync_tree(snap)
            save("SNAPSHOT_VERIFIED")
            service(args, "target-check")
            record["changed"] = True
            save("APPLYING")
            reconcile(stage, root, current, desired, args.restore_mode)
            result = inventory(root, roots, data["excludes"])
            expected_after = desired if args.restore_mode == "replace-managed" else dict(desired, **current)
            require(comparable(result) == comparable(expected_after), "target data verification failed")
            sync_selected(root, roots)
            # A supervisor or independent writer may have restarted during copy.
            # Do not publish TARGET_APPLIED or start services until cold again.
            service(args, "target-check")
            save("TARGET_APPLIED")
            print("TARGET_APPLIED=" + args.token)
            desired_file = None
            if args.use_image_services:
                require(data.get("desired_services") is not None, "image desired-state contract missing")
                desired_file = session / "desired-services.txt"
                atomic_bytes(desired_file, ("\n".join(data["desired_services"]) + "\n").encode())
            record["services_started"] = args.service_cycle
            save("START_REQUESTED")
            service(args, "target-resume", desired_file)
            # Post-start writes are expected for databases; file hash checking
            # belongs before startup. The helper verifies actual Running state.
            service(args, "target-complete")
            save("COMPLETED")
            active.unlink()
            root_active.unlink()
            fsync_dir(state)
            if args.service_cycle:
                print("TARGET_SERVICES_READY=" + args.token)
            else:
                print("TARGET_SERVICES_NOT_STARTED=OFFLINE_OR_FIXTURE_REQUIRES_SEPARATE_ACCEPTANCE")
            print("BUSINESS_ACCEPTANCE_PENDING")
        except BaseException:
            try:
                rollback()
            except BaseException as recovery_error:
                save("RECOVERY_REQUIRED")
                print("RECOVERY_REQUIRED=" + args.token + "; use exact-token recover after resolving: " + str(recovery_error), file=sys.stderr)
            raise


def sync_selected(root, roots):
    for rel in roots:
        path = pathlib.Path(root).joinpath(*rel.split("/"))
        no_symlink_ancestors(path)
        if path.is_symlink():
            fsync_dir(path.parent)
        elif path.is_dir():
            sync_tree(path)
            fsync_dir(path.parent)
        elif path.is_file():
            sync_file(path)
            fsync_dir(path.parent)


def root_for_sync(root, roots, session):
    # Kept separate so compensation tests can inject a durability failure.
    sync_selected(root, roots)
    return session


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="operation", required=True)
    source = sub.add_parser("source-preflight", help="read-only source ACL/xattr/capability gate; no payload hashing")
    source.add_argument("source_root")
    source.add_argument("--items-file", required=True)
    source.add_argument("--exclude-file", required=True)
    s = sub.add_parser("seal")
    s.add_argument("image")
    s.add_argument("--source-id", required=True)
    for name in ("arch", "os-id", "os-version", "product", "version"):
        s.add_argument("--" + name, required=True)
    s.add_argument("--exclude-file", required=True)
    s.add_argument("--desired-services")
    v = sub.add_parser("verify")
    v.add_argument("image")
    a = sub.add_parser("archive")
    a.add_argument("image")
    a.add_argument("archive")
    a.add_argument("--prefix", default="")
    u = sub.add_parser("unpack")
    u.add_argument("archive")
    u.add_argument("dest")
    for op in ("plan", "apply", "recover"):
        r = sub.add_parser(op)
        r.add_argument("image")
        for name in ("target-root", "state-dir", "token", "target-id", "source-id", "arch", "os-id", "os-version", "product", "version", "allowlist"):
            r.add_argument("--" + name, required=True)
        r.add_argument("--restore-mode", choices=("merge-preserve", "replace-managed"), default="merge-preserve")
        r.add_argument("--target-mode", choices=("managed", "offline", "fixture"), default="managed")
        r.add_argument("--target-profile")
        r.add_argument("--services-helper")
        r.add_argument("--service-cycle", action="store_true")
        r.add_argument("--service-timeout", type=int, default=600)
        r.add_argument("--use-image-services", action="store_true")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.operation == "source-preflight":
            count = source_preflight(args.source_root, args.items_file, args.exclude_file)
            print("SOURCE_METADATA_PREFLIGHT=PASS; ENTRIES=" + str(count))
            if platform.system() != "Linux":
                print("SOURCE_METADATA_PLATFORM=FIXTURE_ONLY; LINUX_METADATA=NOT_VERIFIED")
        elif args.operation == "seal":
            identity = {k: getattr(args, k) for k in ("source_id",) + IDENTITY_KEYS}
            seal(pathlib.Path(args.image), identity, args.exclude_file, args.desired_services)
            print("IMAGE_SEALED=PASS")
        elif args.operation == "verify":
            verify(pathlib.Path(args.image))
            print("IMAGE_VERIFIED=PASS")
        elif args.operation == "archive":
            archive_image(args.image, args.archive, args.prefix)
            print("ARCHIVE_VERIFIED=PASS")
        elif args.operation == "unpack":
            print("IMAGE_DIR=" + str(unpack(args.archive, args.dest)))
        elif args.operation == "plan":
            data = verify(pathlib.Path(args.image))
            print(json.dumps(validate_args(args, data)[4], sort_keys=True))
        else:
            old = {}
            def interrupted(signum, frame):
                raise InterruptedError("restore interrupted by signal " + str(signum))
            for sig in (signal.SIGINT, signal.SIGTERM):
                old[sig] = signal.signal(sig, interrupted)
            try:
                apply_image(args, recover=args.operation == "recover")
            finally:
                for sig, handler in old.items():
                    signal.signal(sig, handler)
        return 0
    except (Blocked, OSError, ValueError, KeyError, tarfile.TarError, InterruptedError, subprocess.TimeoutExpired) as exc:
        print("BLOCKED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
