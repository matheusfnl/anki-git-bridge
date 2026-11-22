"""GitBridge: snapshot of the complete Anki2 folder to a Git repo."""

import datetime
import os
import shutil
import sqlite3
import subprocess
import re
import threading
import time
import queue
import tempfile
import sys
import secrets
import html
from urllib.parse import urlsplit
if os.name == "nt":
    import msvcrt
else:
    import fcntl
from contextlib import closing
from pathlib import Path

from aqt import gui_hooks, mw
from aqt.qt import (
    QAction, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    QWidget, QProgressBar, QTextEdit, Qt, QTimer, QListWidget,
    QScrollArea, QFrame, QObject, QEvent,
)
from aqt.utils import showInfo, showWarning, tooltip


ADDON = mw.addonManager.addonFromModule(__name__)
DETECTED_ANKI_EXE = str(Path(sys.executable).resolve()) if Path(sys.executable).name.lower() == "anki.exe" else ""
DEFAULTS = {
    "source": str(Path(mw.pm.base).resolve()),
    "destination": str((Path.home() / "AnkiGitHubBackup").resolve()),
    "remote": "",
    "branch": "main",
    "max_file_mib": 95,
    "automatic": False,
    "backup_on_exit": False,
    "reopen_after_backup": bool(DETECTED_ANKI_EXE),
    "anki_executable": DETECTED_ANKI_EXE,
}
_busy = False
_backup_spawned = False
_operation_lock = None
_collection_syncing = False
_media_syncing = False


def anki_sync_in_progress():
    """Collection and media sync may finish at different times."""
    if _collection_syncing or _media_syncing:
        return True
    media = getattr(mw, "media_syncer", None)
    if media is not None:
        try:
            return bool(media.is_syncing())
        except (AttributeError, RuntimeError):
            pass
    return False


def require_sync_idle():
    if not anki_sync_in_progress():
        return True
    showWarning(tr(
        "Anki is still syncing with AnkiWeb (collection or media). Wait until "
        "sync finishes before using Push to Git or restoring a backup.",
        "O Anki ainda está sincronizando com o AnkiWeb (coleção ou mídias). "
        "Aguarde a sincronização terminar antes de usar Push to Git ou restaurar um backup."
    ))
    return False


def refresh_sync_actions():
    busy = anki_sync_in_progress()
    for action in (globals().get("backup_action"), globals().get("full_restore_action"),
                   globals().get("download_restore_action")):
        if action is not None:
            action.setEnabled(not busy)
    toolbar = getattr(mw, "toolbar", None)
    if toolbar is not None:
        try:
            toolbar.redraw()
        except (AttributeError, RuntimeError):
            pass


def collection_sync_started():
    global _collection_syncing
    _collection_syncing = True
    refresh_sync_actions()


def collection_sync_finished():
    global _collection_syncing
    _collection_syncing = False
    refresh_sync_actions()


def media_sync_changed(running):
    global _media_syncing
    _media_syncing = bool(running)
    refresh_sync_actions()


def lock_path():
    folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GitBridge"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "operation.lock"


def acquire_operation_lock():
    """Windows byte-range lock shared with the external PowerShell workers."""
    try:
        handle = open(lock_path(), "a+b")
    except OSError:
        return None
    try:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except (OSError, PermissionError):
        handle.close()
        return None


def release_operation_lock(handle):
    if handle:
        try:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def tr(english, portuguese):
    """Use Anki's selected interface language; English is the fallback."""
    meta = getattr(mw.pm, "meta", {}) or {}
    language = str(meta.get("defaultLang", "") if isinstance(meta, dict) else "")
    if not language:
        try:
            from anki.lang import current_lang
            language = current_lang() if callable(current_lang) else str(current_lang)
        except (ImportError, AttributeError, TypeError):
            pass
    return portuguese if language.lower().replace("-", "_").startswith("pt") else english


class BackupCancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel.is_set():
        raise BackupCancelled("Backup cancelado pelo usuário.")


def protected_git_directory(name):
    """Leave both active and manually renamed Git metadata directories alone."""
    lower = name.lower()
    return (lower == ".git" or lower.startswith(".git-")
            or lower.startswith("git-backup"))


def settings():
    configured = mw.addonManager.getConfig(__name__) or {}
    return {**DEFAULTS, **{k: v for k, v in configured.items() if v != ""}}


def same_remote_repository(left, right):
    """Recognize equivalent Git URLs without assuming different hosts are equal."""
    def canonical(url):
        url = url.strip()
        if re.match(r"^[^/@]+@[^/:]+:", url):
            host, path = url.split(":", 1)
            host = host.rsplit("@", 1)[1].lower()
        elif "://" in url:
            parsed = urlsplit(url)
            host, path = (parsed.hostname or "").lower(), parsed.path
        else:
            return url.rstrip("/").removesuffix(".git")
        return host, path.strip("/").removesuffix(".git")
    return canonical(left) == canonical(right)


def confirm_remote_update(dest, remote):
    """Return whether updating origin is allowed, or None if user cancels."""
    if not (dest / ".git").exists():
        return False
    existing = command(["remote", "get-url", "origin"], dest, allow_failure=True)
    old = existing.stdout.strip() if existing.returncode == 0 else ""
    if not old or old == remote:
        return False
    if same_remote_repository(old, remote):
        return True  # Formatting/protocol differs, but destination is identical.
    def redact(value):
        return re.sub(r"https?://[^\s/@]+:[^\s/@]+@", "https://***@", value)
    from aqt.utils import askUser
    accepted = askUser(tr(
        "The Git remote in the backup folder differs from the configured repository.\n\n"
        f"Current origin: {redact(old)}\nConfigured URL: {redact(remote)}\n\n"
        "Update origin to the configured URL before pushing?",
        "O remoto Git da pasta de backup é diferente do repositório configurado.\n\n"
        f"Origin atual: {redact(old)}\nURL configurada: {redact(remote)}\n\n"
        "Deseja atualizar o origin para a URL configurada antes do push?"
    ))
    return True if accepted else None


def save_settings(data):
    mw.addonManager.writeConfig(__name__, data)


def command(args, cwd, allow_failure=False, report=None, cancel=None):
    cancel = cancel or threading.Event()
    check_cancel(cancel)
    startup = None
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    process = subprocess.Popen(
        ["git", *args], cwd=str(cwd), text=True, encoding="utf-8",
        errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, startupinfo=startup,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    output_queue = queue.Queue()
    output_lines = []

    def read_output():
        for line in process.stdout:
            output_queue.put(line)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    started = time.monotonic()
    try:
        while process.poll() is None or not output_queue.empty() or reader.is_alive():
            if cancel.is_set():
                raise BackupCancelled("Backup cancelado pelo usuário.")
            if time.monotonic() - started > 180:
                raise TimeoutError(f"O comando git {args[0]} excedeu 180 segundos.")
            try:
                line = output_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            output_lines.append(line)
            if report and args[0] in ("push", "commit", "add"):
                safe_line = re.sub(r"https?://[^\s/@]+:[^\s/@]+@", "https://***@", line.strip())
                if safe_line:
                    report(safe_line)
    except (BackupCancelled, TimeoutError):
        process.kill()
        process.wait()
        raise
    output = "".join(output_lines)
    result = subprocess.CompletedProcess(args, process.returncode, output, "")
    if report:
        safe_action = " ".join(args[:2]) if args[0] != "commit" else "commit"
        report(f"$ git {safe_action}")
        output = result.stdout.strip()
        # Never print embedded HTTP credentials or access tokens in the log.
        output = re.sub(r"https?://[^\s/@]+:[^\s/@]+@", "https://***@", output)
        if output and args[0] not in ("push", "commit", "add"):
            report(output[-5000:])
    if result.returncode and not allow_failure:
        raise RuntimeError(f"git {args[0]}: falhou (código {result.returncode}). Veja o log acima.")
    return result


def validate(data):
    source = Path(data["source"]).expanduser().resolve()
    dest = Path(data["destination"]).expanduser().resolve()
    if not source.is_dir() or not (source / "prefs21.db").exists():
        # prefs21.db is common, but older profiles can have a different layout.
        if not source.is_dir() or not any(source.glob("*/collection.anki*")):
            raise ValueError("A origem não parece ser uma pasta Anki2 válida.")
    if source == dest or source in dest.parents or dest in source.parents:
        raise ValueError("A pasta de backup precisa ficar fora da pasta Anki2.")
    if not data["branch"].strip():
        raise ValueError("Informe a branch de destino.")
    if not data["remote"].strip():
        raise ValueError("Informe a URL do repositório Git remoto.")
    try:
        max_file_mib = int(data.get("max_file_mib", 95))
    except (TypeError, ValueError):
        raise ValueError("O limite de arquivo deve ser um número inteiro em MiB.") from None
    if not 0 <= max_file_mib <= 100000:
        raise ValueError("O limite de arquivo deve estar entre 0 e 100000 MiB.")
    return source, dest


def copy_database(source, destination, cancel, on_progress):
    destination.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with tempfile.NamedTemporaryFile(prefix="anki-sqlite-", suffix=".tmp", dir=destination.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=5)) as original:
            register_unicase(original)
            with closing(sqlite3.connect(str(temporary_path), timeout=5)) as target_db:
                register_unicase(target_db)
                last_pages = 0
                def progress(status, remaining, total):
                    nonlocal last_pages
                    check_cancel(cancel)
                    if time.monotonic() - started > 600:
                        raise TimeoutError(f"Backup SQLite excedeu 10 minutos: {source.name}")
                    pages = max(0, total - remaining)
                    on_progress(max(0, pages - last_pages) * 4096)
                    last_pages = pages
                original.backup(target_db, pages=128, sleep=0.05, progress=progress)
        check_cancel(cancel)
        with closing(sqlite3.connect(f"file:{temporary_path.as_posix()}?mode=ro", uri=True)) as verified:
            register_unicase(verified)
            if verified.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError(f"Cópia do banco {source.name} falhou na verificação de integridade.")
        shutil.copystat(source, temporary_path)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path.exists():
            try:
                temporary_path.unlink()
            except PermissionError:
                # An antivirus or indexing process may still have the temporary
                # file open; preserve the original exception if there was one.
                pass


def register_unicase(connection):
    """Register Anki's collation name for read-only checks of copied indexes.

    Python casefold is not identical to the Rust UniCase comparator at every
    Unicode edge case, so a rare false-positive integrity failure is possible.
    """
    def compare(left, right):
        lhs, rhs = left.casefold(), right.casefold()
        return (lhs > rhs) - (lhs < rhs)

    connection.create_collation("unicase", compare)


def is_sqlite_database(path):
    with path.open("rb") as file:
        return file.read(16) == b"SQLite format 3\x00"


def snapshot(source, dest, report=None, cancel=None):
    """Copy all Anki data while using SQLite backup for open database files.

    This isn't an atomic snapshot of changing media files; the collection DB is
    copied consistently, and transient SQLite journals are excluded.
    """
    suffixes = ("-wal", "-shm", "-journal")
    copied = 0
    skipped = 0
    bytes_done = 0
    last_update = 0.0
    cancel = cancel or threading.Event()

    def update(path, count_delta=0):
        nonlocal bytes_done, last_update
        bytes_done += count_delta
        now = time.monotonic()
        if report and (now - last_update > 0.4):
            last_update = now
            report({"kind": "copy", "file": str(path.relative_to(source)),
                    "files": copied, "bytes": bytes_done})

    for root, dirs, files in os.walk(source):
        check_cancel(cancel)
        root_path = Path(root)
        dirs[:] = [d for d in dirs if not protected_git_directory(d) and d != "__pycache__"]
        relative = root_path.relative_to(source)
        target = dest / relative
        target.mkdir(parents=True, exist_ok=True)
        for filename in files:
            check_cancel(cancel)
            if filename.endswith(suffixes):
                continue
            original = root_path / filename
            output = target / filename
            if original.is_symlink():
                continue  # Do not follow links outside the Anki data directory.
            collection_filename = filename.startswith("collection.anki") and filename.endswith((".anki2", ".anki21b"))
            is_collection_db = collection_filename and is_sqlite_database(original)
            if not is_collection_db and output.is_file():
                source_stat = original.stat()
                output_stat = output.stat()
                if (source_stat.st_size == output_stat.st_size
                        and source_stat.st_mtime_ns == output_stat.st_mtime_ns):
                    skipped += 1
                    continue
            update(original)
            if is_collection_db:
                try:
                    copy_database(original, output, cancel, lambda amount: update(original, amount))
                except sqlite3.DatabaseError as exc:
                    relative_name = original.relative_to(source)
                    raise RuntimeError(
                        f"Não foi possível criar cópia consistente de {relative_name}: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
            else:
                if collection_filename and report:
                    report(f"Arquivo não SQLite preservado: {original.relative_to(source)}")
                with original.open("rb") as input_file, output.open("wb") as output_file:
                    while True:
                        check_cancel(cancel)
                        chunk = input_file.read(1024 * 1024)
                        if not chunk:
                            break
                        output_file.write(chunk)
                        update(original, len(chunk))
                shutil.copystat(original, output)
            copied += 1

    # Reflect deletions in the source without touching the git internals.
    for root, dirs, files in os.walk(dest, topdown=False):
        check_cancel(cancel)
        folder = Path(root)
        if any(protected_git_directory(part) for part in folder.relative_to(dest).parts):
            continue
        relative = folder.relative_to(dest)
        corresponding = source / relative
        for filename in files:
            if filename == ".gitignore" and folder == dest:
                continue
            if not (corresponding / filename).exists() or filename.endswith(suffixes):
                (folder / filename).unlink()
        for dirname in dirs:
            item = folder / dirname
            if protected_git_directory(dirname) or item.is_symlink():
                continue
            if not (corresponding / dirname).exists():
                shutil.rmtree(item)
    if report:
        report(f"Cópia incremental concluída: {copied} atualizados, {skipped} inalterados; {bytes_done / 1048576:.1f} MiB copiados.")


def configure_repo(dest, data, report=None, cancel=None):
    command(["--version"], dest, report=report, cancel=cancel)
    if not (dest / ".git").exists():
        # Never initialize a repository inside an existing unrelated git worktree.
        probe = command(["rev-parse", "--show-toplevel"], dest, allow_failure=True, cancel=cancel)
        if probe.returncode == 0:
            raise RuntimeError("A pasta de backup está dentro de outro repositório Git.")
        command(["init"], dest, report=report, cancel=cancel)
    existing = command(["remote", "get-url", "origin"], dest, allow_failure=True, cancel=cancel)
    remote = data["remote"].strip()
    if existing.returncode:
        command(["remote", "add", "origin", remote], dest, report=report, cancel=cancel)
    elif existing.stdout.strip() != remote:
        if data.get("allow_remote_update", False):
            command(["remote", "set-url", "origin", remote], dest, report=report, cancel=cancel)
            if report:
                report("A URL do remoto 'origin' foi atualizada conforme sua confirmação.")
        else:
            raise RuntimeError("O remoto 'origin' já existe com outra URL. Confirme a mudança em Push to Git antes de continuar.")


def backup_job(data, report, cancel):
    report("ETAPA: Verificando as pastas e o Git")
    source, dest = validate(data)
    dest.mkdir(parents=True, exist_ok=True)
    configure_repo(dest, data, report, cancel)
    report("ETAPA: Copiando os dados do Anki")
    snapshot(source, dest, report, cancel)
    report("ETAPA: Preparando alterações (git add)")
    command(["add", "-A"], dest, report=report, cancel=cancel)
    changes = command(["diff", "--cached", "--quiet"], dest, allow_failure=True, cancel=cancel)
    if changes.returncode not in (0, 1):
        raise RuntimeError(changes.stderr)
    if changes.returncode == 1:
        report("ETAPA: Criando commit")
        stamp = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        command(["commit", "-m", f"Anki backup {stamp}"], dest, report=report, cancel=cancel)
    else:
        report("Nenhuma alteração local para criar commit.")
    branch = data["branch"].strip()
    report("ETAPA: Enviando para o repositório Git (git push)")
    command(["push", "-u", "origin", f"HEAD:refs/heads/{branch}"], dest, report=report, cancel=cancel)
    return "Backup enviado para o repositório Git com sucesso."


class ProgressDialog(QDialog):
    def __init__(self, parent, cancel):
        super().__init__(parent)
        self.cancel = cancel
        self.started = time.monotonic()
        self.last_activity = self.started
        self.current_file = ""
        self.file_count = 0
        self.byte_count = 0
        self.done = False
        self.setWindowTitle("GitBridge — Em andamento")
        self.resize(580, 360)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        layout = QVBoxLayout(self)
        self.stage = QLabel("Preparando o backup…")
        layout.addWidget(self.stage)
        self.details = QLabel("Tempo decorrido: 0s")
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        layout.addWidget(self.bar)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)
        actions = QHBoxLayout()
        self.hide_button = QPushButton("Ocultar")
        self.hide_button.clicked.connect(self.hide)
        actions.addWidget(self.hide_button)
        self.cancel_button = QPushButton("Cancelar backup")
        self.cancel_button.clicked.connect(self.request_cancel)
        actions.addWidget(self.cancel_button)
        layout.addLayout(actions)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)

    def tick(self):
        elapsed = int(time.monotonic() - self.started)
        idle = int(time.monotonic() - self.last_activity)
        copied = self.byte_count / 1048576
        status = (f"{self.file_count} arquivos • {copied:.1f} MiB • "
                  f"Tempo decorrido: {elapsed // 60}m {elapsed % 60:02d}s")
        if self.current_file:
            status += f"\nArquivo atual: {self.current_file}"
        if idle >= 60 and not self.done:
            status += f"\nSem atualização há {idle}s — aguardando a operação atual."
        self.details.setText(status)

    def request_cancel(self):
        self.cancel.set()
        self.cancel_button.setEnabled(False)
        self.stage.setText("Cancelando com segurança…")
        self.log.append("Cancelamento solicitado; aguardando a operação atual parar.")

    def progress(self, message):
        self.last_activity = time.monotonic()
        if isinstance(message, dict) and message.get("kind") == "copy":
            self.current_file = message["file"]
            self.file_count = message["files"]
            self.byte_count = message["bytes"]
            self.tick()
            return
        if message.startswith("ETAPA: "):
            self.stage.setText(message[7:])
            self.current_file = ""
        self.log.append(message)

    def finish(self, message, failed=False):
        self.done = True
        self.timer.stop()
        self.stage.setText("Falha no backup" if failed else "Backup concluído")
        self.log.append(message)
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.hide_button.setText("Fechar")
        self.hide_button.clicked.disconnect()
        self.hide_button.clicked.connect(self.accept)
        self.cancel_button.setEnabled(False)
        self.tick()
        self.show()

    def closeEvent(self, event):
        # Closing the dialog hides it; the background operation continues.
        if not self.done:
            event.ignore()
            self.hide()
        else:
            super().closeEvent(event)


def run_backup():
    global _busy, _operation_lock
    if not require_sync_idle():
        return
    if _busy:
        tooltip("Já existe um backup em andamento.")
        return
    if not shutil.which("git"):
        missing_git_dialog()
        return
    data = settings()
    try:
        source, dest = validate(data)
    except Exception as exc:
        showWarning(f"Configure o GitBridge em Ferramentas primeiro.\n\n{exc}")
        return
    from aqt.utils import askUser
    if not askUser(tr(
        "GitBridge will copy the complete Anki2 folder to the separate Git backup "
        "directory, create a commit if needed, and push to the configured repository.\n\n"
        "Anki will stay open. The original Anki2 will not be modified.\n\n"
        f"Repository: {data['remote'].strip()}\n"
        f"Branch: {data['branch'].strip()}\n\n"
        "Continue with Push to Git?",
        "O GitBridge copiará toda a pasta Anki2 para a pasta separada de backup Git, "
        "criará um commit se necessário e enviará para o repositório configurado.\n\n"
        "O Anki permanecerá aberto. A Anki2 original não será modificada.\n\n"
        f"Repositório: {data['remote'].strip()}\n"
        f"Branch: {data['branch'].strip()}\n\n"
        "Deseja continuar com Push to Git?"
    )):
        return
    try:
        update = confirm_remote_update(dest, data["remote"].strip())
    except Exception as exc:
        showWarning(str(exc))
        return
    if update is None:
        return
    data["allow_remote_update"] = update
    _operation_lock = acquire_operation_lock()
    if _operation_lock is None:
        showWarning(tr("Another GitBridge operation is already running.",
                       "Outra operação do GitBridge já está em andamento."))
        return
    _busy = True
    cancel = threading.Event()
    progress = ProgressDialog(mw, cancel)
    progress.show()

    def report(message):
        mw.taskman.run_on_main(lambda message=message: progress.progress(message))

    def finished(future):
        global _busy, _operation_lock
        _busy = False
        release_operation_lock(_operation_lock)
        _operation_lock = None
        try:
            progress.finish(future.result())
        except Exception as exc:
            progress.finish(f"ERRO: {exc}", failed=True)

    mw.taskman.run_in_background(lambda: backup_job(data, report, cancel), on_done=finished)


def missing_git_dialog():
    """Show a readable install link in an Anki-themed Qt dialog."""
    dialog = QDialog(mw)
    dialog.setWindowTitle("GitBridge — Git não encontrado")
    layout = QVBoxLayout(dialog)
    title = QLabel("<b>Git não encontrado</b>")
    layout.addWidget(title)
    description = QLabel(
        "O GitBridge precisa do Git instalado para enviar seus backups ao repositório Git."
    )
    description.setWordWrap(True)
    layout.addWidget(description)
    layout.addWidget(QLabel("Instale o Git e tente novamente."))
    link = QLabel('<a href="https://git-scm.com/downloads/win?utm_source=chatgpt.com">Baixar Git</a>')
    link.setOpenExternalLinks(True)
    layout.addWidget(link)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()


def backup_after_exit(automatic=False):
    """Launch a separate Windows process, then close Anki normally."""
    global _backup_spawned
    if _backup_spawned:
        return
    if anki_sync_in_progress():
        if not automatic:
            require_sync_idle()
        else:
            print("GitBridge: backup automático adiado/ignorado porque o AnkiWeb ainda está sincronizando.")
        return
    if os.name != "nt":
        if not automatic:
            showWarning("O backup após fechar o Anki está disponível apenas no Windows nesta versão.")
        return
    if not shutil.which("git"):
        if not automatic:
            missing_git_dialog()
        else:
            # Qt is already shutting down. Avoid launching a dialog at this point.
            print("GitBridge: Git não encontrado; backup automático não iniciado.")
        return
    probe = acquire_operation_lock()
    if probe is None:
        if not automatic:
            showWarning(tr("Another GitBridge operation is already running.",
                           "Outra operação do GitBridge já está em andamento."))
        return
    release_operation_lock(probe)
    data = settings()
    try:
        source, dest = validate(data)
        if not Path(__file__).with_name("backup_worker.ps1").is_file():
            raise RuntimeError("O script de backup não está instalado.")
        reopen_path = ""
        if data.get("reopen_after_backup") and not automatic:
            candidate = Path(data.get("anki_executable", "")).expanduser()
            if not candidate.is_file() or candidate.suffix.lower() != ".exe":
                raise ValueError("Informe o caminho do Anki.exe ou desmarque 'Reabrir Anki'.")
            reopen_path = str(candidate.resolve())
    except Exception as exc:
        if not automatic:
            showWarning(f"Verifique as configurações do GitBridge:\n\n{exc}")
        else:
            print(f"GitBridge: backup automático ignorado: {exc}")
        return
    if not automatic:
        from aqt.utils import askUser
        if not askUser(tr(
            "To back up the entire Anki2 folder, Anki must close normally.\n\n"
            "Once it closes, GitBridge will copy the data, commit any changes, "
            "and push to Git. Anki will reopen if enabled in Settings.\n\n"
            f"Repository: {data['remote'].strip()}\nBranch: {data['branch'].strip()}\n\n"
            "Continue with Push to Git?",
            "Para fazer backup de toda a pasta Anki2, o Anki precisa fechar normalmente.\n\n"
            "Depois do fechamento, o GitBridge copiará os dados, criará um commit "
            "se necessário e fará o push. O Anki será reaberto se essa opção estiver ativada.\n\n"
            f"Repositório: {data['remote'].strip()}\nBranch: {data['branch'].strip()}\n\n"
            "Deseja continuar com Push to Git?"
        )):
            return
        try:
            update_remote = confirm_remote_update(dest, data["remote"].strip())
        except Exception as exc:
            showWarning(str(exc))
            return
        if update_remote is None:
            return
        data["allow_remote_update"] = update_remote
    script = Path(__file__).with_name("backup_worker.ps1")
    from aqt.theme import theme_manager
    mode = "dark" if theme_manager.night_mode else "light"
    qt_script = Path(__file__).with_name("backup_qt.py")
    packaged_gui = Path(__file__).with_name("backup_gui") / "backup_gui.exe"
    qt_python = None
    # Anki may bundle Qt without exposing a stand-alone Python interpreter.
    # Probe independently; never assume that anki.exe accepts Python's -c flag.
    if qt_script.is_file():
        candidates = [Path(sys.prefix) / "pythonw.exe", Path(sys.prefix) / "python.exe"]
        for name in ("pythonw.exe", "python.exe"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
        for executable in dict.fromkeys(candidates):
            if not executable.is_file():
                continue
            try:
                checked = subprocess.run(
                    [str(executable), "-c", "import PyQt6.QtWidgets"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=4,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if checked.returncode == 0:
                    qt_python = str(executable)
                    break
            except (OSError, subprocess.TimeoutExpired):
                continue
    try:
        base_args = ["-Source", str(source), "-Destination", str(dest),
                     "-Remote", data["remote"].strip(), "-Branch", data["branch"].strip(),
                     "-AnkiProcessId", str(os.getpid()), "-ReopenPath", reopen_path,
                     "-MaxFileMiB", str(int(data.get("max_file_mib", 95))),
                     "-Theme", mode, "-Language", "pt-BR" if tr("en", "pt") == "pt" else "en"]
        if data.get("allow_remote_update"):
            base_args.append("-AllowRemoteUpdate")
        icon_args = ["--anki-icon", DETECTED_ANKI_EXE] if DETECTED_ANKI_EXE else []
        icon_args += ["--language", "pt-BR" if tr("en", "pt") == "pt" else "en"]
        if packaged_gui.is_file():
            args = [str(packaged_gui), "--worker", str(script), *icon_args, *base_args]
        elif qt_python:
            args = [qt_python, str(qt_script), "--worker", str(script), *icon_args, *base_args]
        else:
            args = ["powershell.exe", "-NoProfile", "-STA", "-WindowStyle", "Hidden",
                    "-ExecutionPolicy", "Bypass", "-File", str(script), *base_args]
        subprocess.Popen(
            args,
            creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    except Exception as exc:
        if not automatic:
            showWarning(f"Não foi possível iniciar o processo de backup:\n{exc}")
        else:
            print(f"GitBridge: falha ao iniciar backup automático: {exc}")
        return
    _backup_spawned = True
    if not automatic:
        mw.close()


def on_application_about_to_quit():
    if settings().get("backup_on_exit", False):
        backup_after_exit(automatic=True)


def add_toolbar_backup_link(links, toolbar):
    link = toolbar.create_link(
        "anki_github_backup_now", "Push to Git", lambda: backup_after_exit(False),
        tip=tr("Close Anki, back up the complete Anki2 folder and push to Git",
               "Fechar o Anki, fazer backup da Anki2 completa e enviar ao Git"),
        id="anki-github-backup",
    )
    if anki_sync_in_progress():
        hint = tr("Wait for AnkiWeb sync to finish",
                  "Aguarde a sincronização com o AnkiWeb terminar")
        link = ("<span style='opacity:0.45;pointer-events:none;cursor:not-allowed' "
                f"title='{html.escape(hint, quote=True)}'>" + link + "</span>")
    links.append(link)


class HistoryDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("GitBridge — Histórico")
        self.resize(760, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Selecione uma execução para visualizar o log:"))
        self.items = QListWidget()
        self.files = sorted(
            (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AnkiGitHubBackup" / "logs").glob("*.log"),
            reverse=True,
        )[:100]
        for item in self.files:
            self.items.addItem(item.name)
        layout.addWidget(self.items)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        self.items.currentRowChanged.connect(self.open_log)
        if self.files:
            self.items.setCurrentRow(0)
        else:
            self.details.setPlainText("Ainda não há logs de backup neste computador.")

    def open_log(self, index):
        if 0 <= index < len(self.files):
            try:
                self.details.setPlainText(self.files[index].read_text(encoding="utf-8-sig", errors="replace")[-150000:])
            except OSError as exc:
                self.details.setPlainText(f"Não foi possível abrir o log: {exc}")


def show_history():
    HistoryDialog(mw).exec()


def prepare_restore():
    """Clone remote backup into a new directory, never overwrite the current Anki2."""
    global _busy, _operation_lock
    if not require_sync_idle():
        return
    if _busy:
        showWarning(tr("Another GitBridge operation is already running.",
                       "Outra operação do GitBridge já está em andamento."))
        return
    data = settings()
    remote = data.get("remote", "").strip()
    branch = data.get("branch", "main").strip()
    if not remote:
        showWarning("Configure primeiro a URL do repositório Git remoto.")
        return
    parent = QFileDialog.getExistingDirectory(mw, "Escolha onde preparar a restauração")
    if not parent:
        return
    folder = Path(parent) / ("Anki2-restaurado-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    if folder.exists():
        showWarning("A pasta de restauração já existe. Selecione outro destino.")
        return
    from aqt.utils import askUser
    if not askUser(
        "Esta opção apenas BAIXA uma cópia dos arquivos do repositório Git.\n"
        f"Destino:\n{folder}\n\n"
        "A Anki2 original NÃO será substituída. Deseja continuar?"
    ):
        return
    _operation_lock = acquire_operation_lock()
    if _operation_lock is None:
        showWarning(tr("Another GitBridge operation is already running.",
                       "Outra operação do GitBridge já está em andamento."))
        return
    _busy = True
    cancel = threading.Event()
    progress = ProgressDialog(mw, cancel)
    progress.show()

    def report(message):
        mw.taskman.run_on_main(lambda message=message: progress.progress(message))

    def task():
        report("ETAPA: Clonando backup do repositório Git")
        command(["clone", "--single-branch", "-b", branch, remote, str(folder)],
                Path(parent), report=report, cancel=cancel)
        return f"Backup recuperado em {folder}.\nFeche o Anki antes de substituir qualquer arquivo."

    def finished(future):
        global _busy, _operation_lock
        _busy = False
        release_operation_lock(_operation_lock)
        _operation_lock = None
        try:
            progress.finish(future.result())
        except Exception as exc:
            progress.finish(f"ERRO ao recuperar backup: {exc}", failed=True)

    mw.taskman.run_in_background(task, on_done=finished)


def restore_entire_anki2():
    """Explicitly confirmed whole-directory replacement, performed after exit."""
    global _backup_spawned
    if not require_sync_idle():
        return
    if _backup_spawned:
        showWarning("Já existe uma operação do GitBridge em andamento.")
        return
    if os.name != "nt":
        showWarning("A restauração automática está disponível apenas no Windows nesta versão.")
        return
    if not shutil.which("git"):
        missing_git_dialog()
        return
    probe = acquire_operation_lock()
    if probe is None:
        showWarning(tr("Another GitBridge operation is already running.",
                       "Outra operação do GitBridge já está em andamento."))
        return
    release_operation_lock(probe)
    data = settings()
    try:
        source, dest = validate(data)
        script = Path(__file__).with_name("restore_worker.ps1")
        if not script.is_file():
            raise FileNotFoundError("O componente de restauração não está instalado.")
        if not (Path(__file__).with_name("backup_gui") / "backup_gui.exe").is_file():
            raise RuntimeError("A restauração completa exige a versão Windows compilada do GitBridge.")
        if not source.name.lower() == "anki2":
            raise ValueError("Por segurança, a origem da restauração precisa ser uma pasta chamada Anki2.")
        if not source.parent.is_dir():
            raise ValueError("A pasta pai da Anki2 não existe.")
    except Exception as exc:
        showWarning(f"Verifique as configurações antes de restaurar:\n\n{exc}")
        return

    first = QDialog(mw)
    first.setWindowTitle(tr("GitBridge — WARNING: full replacement",
                            "GitBridge — ATENÇÃO: substituição completa"))
    first.resize(615, 300)
    first_layout = QVBoxLayout(first)
    warning = QLabel(tr(
        "<b>WARNING: THIS WILL REPLACE THE ENTIRE Anki2 FOLDER.</b><br><br>"
        "GitBridge will download the <b>latest version of the configured branch</b> "
        "from your Git repository and restore its files.<br><br>"
        "This includes ALL users and profiles, decks, cards, study history, media, "
        "add-ons, settings and other local files.<br><br>"
        "<b>Exception:</b> the currently installed GitBridge version and its settings "
        "will be preserved. Other add-ons will be restored from Git.<br><br>"
        "The current Anki2 will be moved to a separate permanent backup folder. "
        "Anki must close before the replacement.",
        "<b>ATENÇÃO: ESTA OPERAÇÃO SUBSTITUIRÁ TODA A PASTA Anki2.</b><br><br>"
        "O GitBridge buscará a <b>versão mais recente da branch configurada</b> "
        "no repositório Git e restaurará esses arquivos.<br><br>"
        "Isso inclui TODOS os usuários e perfis, baralhos, cartões, histórico de estudos, "
        "mídias, extensões, configurações e demais arquivos locais.<br><br>"
        "<b>Exceção:</b> a versão atual do GitBridge e suas configurações serão preservadas. "
        "As demais extensões serão restauradas a partir do Git.<br><br>"
        "O estado atual será movido para uma pasta de segurança ao lado da Anki2, "
        "sem ser apagado automaticamente. Você terá que fechar o Anki para restaurar."
    ))
    warning.setWordWrap(True)
    first_layout.addWidget(warning)
    first_layout.addWidget(QLabel(f"<b>{tr('Folder to replace', 'Pasta que será substituída')}:</b> "
                                  f"{html.escape(str(source))}"))
    first_layout.addWidget(QLabel(
        f"<b>{tr('Repository', 'Repositório')}:</b> {html.escape(data['remote'].strip())}<br>"
        f"<b>Branch:</b> {html.escape(data['branch'].strip())}"
    ))
    first_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    continue_button = first_buttons.addButton(tr("Continue", "Continuar"), QDialogButtonBox.ButtonRole.AcceptRole)
    continue_button.clicked.connect(first.accept)
    first_buttons.rejected.connect(first.reject)
    first_layout.addWidget(first_buttons)
    if first.exec() != QDialog.DialogCode.Accepted:
        return

    confirmation = secrets.token_hex(4).upper()
    second = QDialog(mw)
    second.setWindowTitle(tr("GitBridge — Final confirmation", "GitBridge — Confirmação final"))
    second_layout = QVBoxLayout(second)
    second_layout.addWidget(QLabel(tr(
        "To confirm replacement of the ENTIRE Anki2 folder, type exactly:",
        "Para confirmar a substituição de TODA a Anki2, digite exatamente:"
    )))
    token = QLabel(f"<b>{confirmation}</b>")
    second_layout.addWidget(token)
    typed = QLineEdit()
    typed.setPlaceholderText(tr("Type the code above", "Digite o código acima"))
    second_layout.addWidget(typed)
    second_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    confirm_button = second_buttons.addButton(tr("Replace entire Anki2", "Substituir toda a Anki2"),
                                              QDialogButtonBox.ButtonRole.AcceptRole)
    confirm_button.setEnabled(False)
    typed.textChanged.connect(lambda value: confirm_button.setEnabled(value == confirmation))
    confirm_button.clicked.connect(second.accept)
    second_buttons.rejected.connect(second.reject)
    second_layout.addWidget(second_buttons)
    if second.exec() != QDialog.DialogCode.Accepted or typed.text() != confirmation:
        return

    # Use a separately hosted script: the add-on itself is inside Anki2.
    from aqt.theme import theme_manager
    mode = "dark" if theme_manager.night_mode else "light"
    standalone = Path(__file__).with_name("backup_gui") / "backup_gui.exe"
    qt_script = Path(__file__).with_name("backup_qt.py")
    base_args = ["-Source", str(source), "-Remote", data["remote"].strip(),
                 "-Branch", data["branch"].strip(), "-AnkiProcessId", str(os.getpid()),
                 "-CurrentAddonPath", str(Path(__file__).resolve().parent),
                 "-ReopenPath", data.get("anki_executable", ""), "-Theme", mode,
                 "-Language", "pt-BR" if tr("en", "pt") == "pt" else "en"]
    try:
        anki_icon = DETECTED_ANKI_EXE or data.get("anki_executable", "")
        icon_args = (["--anki-icon", anki_icon]
                     if anki_icon and Path(anki_icon).is_file() else [])
        args = [str(standalone), "--worker", str(script), "--restore",
                "--language", "pt-BR" if tr("en", "pt") == "pt" else "en",
                *icon_args, *base_args]
        subprocess.Popen(
            args,
            creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    except Exception as exc:
        showWarning(f"Não foi possível iniciar a restauração: {exc}")
        return
    _backup_spawned = True
    mw.close()


class ProtectManualBackupClose(QObject):
    def eventFilter(self, watched, event):
        if watched is mw and event.type() == QEvent.Type.Close and _busy:
            showWarning(tr(
                "A GitBridge backup is still running. Wait for it to finish or cancel it "
                "before closing Anki. A forced shutdown can interrupt the backup.",
                "Um backup do GitBridge ainda está em andamento. Aguarde a conclusão "
                "ou cancele antes de fechar o Anki. Um encerramento forçado pode interromper o backup."
            ))
            event.ignore()
            return True
        return False


class SettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("GitBridge — Configurações")
        self.resize(730, 590)
        self.setMinimumSize(590, 420)
        data = settings()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(4, 4, 4, 4)
        content.setSpacing(14)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        self.source = QLineEdit(data["source"])
        self.destination = QLineEdit(data["destination"])
        self.remote = QLineEdit(data["remote"])
        self.branch = QLineEdit(data["branch"])
        self.max_file_mib = QLineEdit(str(data.get("max_file_mib", 95)))
        self.anki_executable = QLineEdit(data.get("anki_executable", ""))

        def section(title):
            frame = QFrame()
            frame.setObjectName("gitbridgeSection")
            frame.setStyleSheet(
                "QFrame#gitbridgeSection { border: 1px solid palette(mid); border-radius: 6px; }"
            )
            inner = QVBoxLayout(frame)
            inner.setContentsMargins(12, 10, 12, 12)
            inner.setSpacing(9)
            inner.addWidget(QLabel(f"<b>{title}</b>"))
            return frame, inner

        def file_selector(widget, callback):
            container = QWidget()
            row = QHBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(widget)
            browse_button = QPushButton("Procurar…")
            browse_button.clicked.connect(callback)
            row.addWidget(browse_button)
            return container, browse_button

        source_row, _ = file_selector(self.source, lambda: self.browse(self.source))
        dest_row, _ = file_selector(self.destination, lambda: self.browse(self.destination))
        self.executable_row, self.executable_browse = file_selector(
            self.anki_executable, self.browse_executable)

        backup_section, backup_layout = section("Backup para Git")
        backup_form = QFormLayout()
        backup_form.addRow("Pasta Anki2", source_row)
        backup_form.addRow("Pasta de backup Git", dest_row)
        backup_form.addRow("URL do repositório Git", self.remote)
        backup_form.addRow("Branch", self.branch)
        backup_layout.addLayout(backup_form)
        content.addWidget(backup_section)

        behavior_section, behavior_layout = section("Comportamento")
        behavior_layout.addWidget(QLabel("Backup manual: botão Push to Git, ao lado de Sincronizar."))
        self.on_exit = QCheckBox("Fazer backup automaticamente ao fechar o Anki")
        self.on_exit.setChecked(bool(data.get("backup_on_exit", False)))
        behavior_layout.addWidget(self.on_exit)
        self.reopen = QCheckBox("Reabrir o Anki após a tentativa de backup")
        self.reopen.setChecked(bool(data.get("reopen_after_backup")))
        behavior_layout.addWidget(self.reopen)
        executable_form = QFormLayout()
        executable_form.addRow("Executável do Anki", self.executable_row)
        behavior_layout.addLayout(executable_form)
        self.reopen.toggled.connect(self.executable_row.setEnabled)
        self.executable_row.setEnabled(self.reopen.isChecked())
        behavior_layout.addWidget(QLabel("No fechamento automático, o Anki não será reaberto."))
        content.addWidget(behavior_section)

        advanced_section, advanced_layout = section("Avançado")
        advanced_form = QFormLayout()
        advanced_form.addRow("Limite de arquivo (MiB, 0 = desativado)", self.max_file_mib)
        advanced_form.addRow(QLabel("O Git deve estar instalado e autenticado (SSH ou HTTPS)."))
        advanced_layout.addLayout(advanced_form)
        advanced_section.setVisible(False)
        advanced_toggle = QCheckBox("Mostrar opções avançadas")
        advanced_toggle.toggled.connect(advanced_section.setVisible)
        content.addWidget(advanced_toggle)
        content.addWidget(advanced_section)

        content.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def browse(self, edit):
        folder = QFileDialog.getExistingDirectory(self, "Selecionar pasta", edit.text())
        if folder:
            edit.setText(folder)

    def browse_executable(self):
        executable, _ = QFileDialog.getOpenFileName(
            self, "Selecionar Anki.exe", self.anki_executable.text(), "Executáveis (*.exe)")
        if executable:
            self.anki_executable.setText(executable)

    def values(self):
        return {
            "source": self.source.text().strip(),
            "destination": self.destination.text().strip(),
            "remote": self.remote.text().strip(),
            "branch": self.branch.text().strip(),
            "max_file_mib": self.max_file_mib.text().strip(),
            "automatic": False,
            "backup_on_exit": self.on_exit.isChecked(),
            "reopen_after_backup": self.reopen.isChecked(),
            "anki_executable": self.anki_executable.text().strip(),
        }

    def save(self):
        data = self.values()
        try:
            validate(data)
        except Exception as exc:
            showWarning(str(exc))
            return
        if data["backup_on_exit"] and not shutil.which("git"):
            missing_git_dialog()
            return
        save_settings(data)
        self.accept()


def open_settings():
    SettingsDialog(mw).exec()


def on_sync_finished():
    pass  # Backups are now started explicitly by the user, never by AnkiWeb sync.


menu = mw.form.menuTools.addMenu("GitBridge")
settings_action = QAction(tr("Settings", "Configurações"), mw)
settings_action.triggered.connect(open_settings)
menu.addAction(settings_action)
backup_action = QAction("Push to Git", mw)
backup_action.triggered.connect(lambda: backup_after_exit(False))
menu.addAction(backup_action)
history_action = QAction(tr("Backup history", "Histórico de backups"), mw)
history_action.triggered.connect(show_history)
menu.addAction(history_action)
download_restore_action = QAction(tr("Download backup from Git", "Baixar backup do Git"), mw)
download_restore_action.triggered.connect(prepare_restore)
menu.addAction(download_restore_action)
full_restore_action = QAction(tr("Restore full backup", "Restaurar backup completo"), mw)
full_restore_action.triggered.connect(restore_entire_anki2)
menu.addAction(full_restore_action)
gui_hooks.top_toolbar_did_init_links.append(add_toolbar_backup_link)
gui_hooks.sync_will_start.append(collection_sync_started)
gui_hooks.sync_did_finish.append(collection_sync_finished)
gui_hooks.media_sync_did_start_or_stop.append(media_sync_changed)
mw.app.aboutToQuit.connect(on_application_about_to_quit)
_close_guard = ProtectManualBackupClose(mw)
mw.installEventFilter(_close_guard)
refresh_sync_actions()
