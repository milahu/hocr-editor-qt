import os
import shutil
import subprocess
import time
from datetime import datetime

def find_git_root(path: str) -> str | None:
    """Walk up parent directories until .git is found."""
    cur = os.path.abspath(path)
    while cur and cur != os.path.dirname(cur):
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        cur = os.path.dirname(cur)
    return None

def is_file_tracked(git_root: str, relpath: str) -> bool:
    """Return True if file is tracked by git."""
    try:
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", relpath],
            cwd=git_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False

def git_commit(hocr_file: str, git_root: str, relpath: str):
    """Commit current file to git, handling staged changes with stash."""
    # check for staged changes
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=git_root,
        capture_output=True,
        text=True,
    )
    staged_files = []
    for line in proc.stdout.splitlines():
        line = line.rstrip()
        if line[:2] in ("M ", "A ", "D "):
            staged_files.append(line[3:])

    stashed = False
    stash_item_id = None
    if staged_files:
        with open(hocr_file, "rb") as fp:
            hocr_bytes = fp.read()
        other_staged_files = filter(lambda f: f != relpath, staged_files)
        date_str = datetime.now().strftime("%FT%T.%f") + time.strftime("Z%z")
        stash_message = f"hocr-editor save hocr_file {date_str}"
        args = [
            "git",
            "stash",
            "push",
            "--message", stash_message,
            "--",
            *other_staged_files,
        ]
        proc = subprocess.run(args, cwd=git_root)
        if proc.returncode != 0:
            # nothing was stashed
            pass
        else:
            stashed = True
            # get stash_item_id
            proc = subprocess.run(
                ["git", "stash", "list"],
                cwd=git_root,
                capture_output=True,
                text=True,
            )
            for line in proc.stdout.splitlines():
                line = line.rstrip()
                if not line.startswith("stash@{"): continue
                match = re.match(r"stash@\{([0-9]+)\}: On [^:]+: (.*)")
                if not match: continue
                id, msg = match.groups()
                if msg == stash_message:
                    stash_item_id = id
                    break
            # restore hocr_file
            with open(hocr_file, "rb") as fp:
                hocr_bytes_2 = fp.read()
            if hocr_bytes != hocr_bytes_2:
                # also hocr_file was stashed
                # this should never happen
                # because we only stash other_staged_files
                # but still... this can happen in rare cases
                with open(hocr_file, "wb") as fp:
                    fp.write(hocr_bytes)
            del hocr_bytes_2
        del hocr_bytes

    # add + commit
    proc = subprocess.run(
        ["git", "add", relpath],
        cwd=git_root,
        check=True,
    )

    commit_msg = os.path.basename(hocr_file)
    commit_msg = commit_msg.removesuffix(".hocr").lstrip("0")

    # note: ignore nonzero returncodes
    # for example when there was "nothing to commit"
    subprocess.run(
        ["git", "commit", "-m", commit_msg],
        cwd=git_root,
        stdout=subprocess.DEVNULL,
        # stderr=subprocess.DEVNULL,
    )

    if stashed:
        args = ["git", "stash", "pop"]
        if stash_item_id != None:
            args.append(stash_item_id)
        subprocess.run(args, cwd=git_root, check=True)
