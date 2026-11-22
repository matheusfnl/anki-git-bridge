"""Optional stand-alone Qt frontend; never imports aqt or touches Anki's DB."""

import argparse
import sys
import tempfile
from pathlib import Path
from uuid import uuid4
from PyQt6.QtCore import QProcess, QFileInfo, QTimer
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QProgressBar, QStyleFactory, QFileIconProvider, QMessageBox,
)


class BackupWindow(QWidget):
    def __init__(self, worker, arguments, anki_icon_path="", restore=False, language="en"):
        super().__init__()
        self.restore = restore
        self.pt = language.lower().startswith("pt")
        self.critical = False
        self.dark = (arguments[arguments.index("-Theme") + 1] == "dark"
                     if "-Theme" in arguments else True)
        self.done = False
        self.pending = ""
        self.setWindowTitle("GitBridge — Anki")
        if anki_icon_path:
            try:
                icon = QFileIconProvider().icon(QFileInfo(anki_icon_path))
                if not icon.isNull():
                    self.setWindowIcon(icon)
            except Exception:
                pass
        self.resize(720, 480)
        self.setMinimumSize(610, 380)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(15, 13, 15, 13)
        outer.setSpacing(8)
        title = QLabel(
            ("GitBridge — Restaurando Anki2" if restore else "GitBridge — Em andamento")
            if self.pt else
            ("GitBridge — Restoring Anki2" if restore else "GitBridge — In progress")
        )
        title.setObjectName("title")
        outer.addWidget(title)
        subtitle = QLabel(
            ("SUBSTITUIÇÃO COMPLETA DA Anki2 — todos os perfis e configurações"
             if restore else "Cópia da Anki2 e envio ao repositório Git") if self.pt else
            ("FULL Anki2 REPLACEMENT — all profiles and settings"
             if restore else "Back up Anki2 and push to Git")
        )
        subtitle.setObjectName("muted")
        outer.addWidget(subtitle)
        outer.addSpacing(3)
        self.stage = QLabel("Aguardando o Anki encerrar…" if self.pt else "Waiting for Anki to close…")
        self.stage.setObjectName("stage")
        outer.addWidget(self.stage)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(12)
        outer.addWidget(self.bar)
        caption = QLabel("Registro de atividades" if self.pt else "Activity log")
        caption.setObjectName("muted")
        outer.addWidget(caption)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        outer.addWidget(self.log, 1)
        bottom = QHBoxLayout()
        self.status = QLabel(
            ("A instalação anterior será preservada em uma pasta separada."
             if restore else "A pasta original do Anki não será modificada.") if self.pt else
            ("The previous installation will be preserved in a separate folder."
             if restore else "The original Anki folder will not be modified.")
        )
        self.status.setObjectName("muted")
        bottom.addWidget(self.status, 1)
        self.button = QPushButton(
            ("Cancelar restauração" if restore else "Cancelar backup") if self.pt
            else ("Cancel restore" if restore else "Cancel backup")
        )
        self.button.clicked.connect(self.cancel_or_close)
        self.button.setMinimumWidth(148)
        bottom.addWidget(self.button)
        outer.addLayout(bottom)

        if self.dark:
            self.setStyleSheet("""
                QWidget { background: #282828; color: #eeeeee; font: 9pt 'Segoe UI'; }
                QLabel#title { font-size: 10pt; font-weight: 600; }
                QLabel#stage { font-size: 9pt; }
                QLabel#muted { color: #c6c6c6; }
                QPlainTextEdit { background: #252525; color: #eeeeee; border: 1px solid #383838;
                                 border-radius: 4px; padding: 4px; font: 9pt 'Segoe UI'; }
                QScrollBar:vertical { background: #282828; width: 12px; margin: 0; }
                QScrollBar::handle:vertical { background: #555555; border-radius: 5px; min-height: 28px; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
                QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: #282828; }
                QPushButton { background: #404040; color: white; border: 1px solid #454545;
                              border-radius: 5px; padding: 5px 12px; }
                QPushButton:hover { background: #4b4b4b; }
                QPushButton:focus { border: 1px solid #548bde; }
                QPushButton:disabled { color: #7d7d7d; background: #343434; }
                QProgressBar { background: #353535; border: 1px solid #494949; border-radius: 3px; }
                QProgressBar::chunk { background: #638bc5; border-radius: 3px; }
            """)
        else:
            self.setStyleSheet("""
                QWidget { background: #f5f5f5; color: #282828; font: 9pt 'Segoe UI'; }
                QLabel#title { font-size: 10pt; font-weight: 600; }
                QLabel#stage { font-size: 9pt; }
                QLabel#muted { color: #68707c; }
                QPlainTextEdit { background: white; color: #30333a; border: 1px solid #c5cbd4;
                                 border-radius: 5px; padding: 9px; font: 9pt Consolas; }
                QPushButton { background: #e2e2e2; color: #282828; border: 1px solid #b9b9b9;
                              border-radius: 5px; padding: 8px 14px; }
                QPushButton:hover { background: #dadada; }
                QPushButton:focus { border: 1px solid #548bde; }
                QPushButton:disabled { background: #b8c2d0; }
                QProgressBar { background: #e1e5eb; border: 0; border-radius: 4px; }
                QProgressBar::chunk { background: #3568b3; border-radius: 4px; }
            """)

        self.process = QProcess(self)
        self.cancel_signal = Path(tempfile.gettempdir()) / ("gitbridge-cancel-" + uuid4().hex)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.on_finished)
        self.process.errorOccurred.connect(self.on_error)
        worker_args = [
            "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
            "-File", worker, *arguments, "-CancelSignal", str(self.cancel_signal),
        ]
        if not restore:
            worker_args.append("-ExternalGui")
        self.process.start("powershell.exe", worker_args)

    def read_output(self):
        self.pending += bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        lines = self.pending.split("\n")
        self.pending = lines.pop()
        for line in lines:
            line = line.rstrip("\r")
            if line.startswith("STAGE: "):
                stage = line[7:]
                if not self.pt:
                    stage = {
                        "Baixando backup do Git...": "Downloading backup from Git…",
                        "Validando backup...": "Validating backup…",
                        "Aguardando fechamento do Anki...": "Waiting for Anki to close…",
                        "Preservando instalacao atual...": "Preserving current installation…",
                        "Restaurando Anki2...": "Restoring Anki2…",
                        "Recuperando restauração interrompida...": "Recovering interrupted restore…",
                        "Falha na restauracao": "Restore failed",
                        "Aguardando fechamento do Anki...": "Waiting for Anki to close…",
                        "Copiando dados do Anki...": "Copying Anki data…",
                        "Backup concluido com sucesso": "Backup completed successfully",
                        "Falha no backup": "Backup failed",
                    }.get(stage, stage)
                self.stage.setText(stage)
            elif line == "CRITICAL_BEGIN":
                self.critical = True
                self.button.setEnabled(False)
                self.button.setText("NÃO FECHE A JANELA" if self.pt else "DO NOT CLOSE WINDOW")
            elif line:
                self.log.appendPlainText(line)

    def on_finished(self, exit_code, _status):
        if self.done:
            return
        self.read_output()
        if self.pending:
            self.log.appendPlainText(self.pending)
        self.done = True
        self.cancel_signal.unlink(missing_ok=True)
        self.bar.setRange(0, 100)
        # Worker reports success or failure in its output, not just process exit status.
        output = self.log.toPlainText()
        complete = ("RESTORE_COMPLETED" if self.restore else "BACKUP CONCLUIDO E ENVIADO!") in output
        self.bar.setValue(100 if complete else 0)
        self.stage.setText(
            (("Restauração concluída" if complete else "Restauração não concluída")
             if self.restore else ("Backup concluído" if complete else "Backup não concluído"))
            if self.pt else
            (("Restore complete" if complete else "Restore not completed")
             if self.restore else ("Backup complete" if complete else "Backup not completed"))
        )
        self.status.setText(
            ("A cópia antiga NÃO foi apagada. Consulte o caminho no registro acima."
             if self.restore and complete else
             "Confira os logs antes de fechar." if not complete else "Backup enviado com sucesso.")
            if self.pt else
            ("The previous backup was NOT deleted. See its path in the log."
             if self.restore and complete else
             "Review the logs before closing." if not complete else "Backup pushed successfully.")
        )
        self.button.setText("Fechar" if self.pt else "Close")
        self.button.setEnabled(True)
        if self.restore and complete:
            old_path = next((line.split(": ", 1)[1] for line in output.splitlines()
                             if line.startswith("COPIA_ANTIGA: ")), "")
            self.raise_()
            QMessageBox.information(
                self,
                "GitBridge — Restauração concluída" if self.pt else "GitBridge — Restore completed",
                ("A pasta Anki2 foi restaurada.\n\n"
                 "A versão atual do GitBridge foi preservada.\n\n"
                 f"Sua cópia antiga foi preservada em:\n{old_path}\n\n"
                 "Caso queira apagar o backup antigo, acesse a pasta e apague manualmente.")
                if self.pt else
                ("The Anki2 folder has been restored.\n\n"
                 "Your current GitBridge version was preserved.\n\n"
                 f"Your previous copy was preserved at:\n{old_path}\n\n"
                 "To delete the old backup, open that folder and remove it manually."),
            )
        if complete and "ANKI_REOPENED" in output and not self.restore:
            self.status.setText("Anki reaberto. Fechando esta janela…" if self.pt
                                else "Anki reopened. Closing this window…")
            QTimer.singleShot(1800, self.close)

    def on_error(self, _error):
        if self.process.state() == QProcess.ProcessState.NotRunning and not self.done:
            self.log.appendPlainText("ERRO: Falha ao iniciar PowerShell para executar o backup."
                                     if self.pt else "ERROR: Failed to start PowerShell for GitBridge.")
            self.on_finished(-1, None)

    def cancel_or_close(self):
        if self.done:
            self.close()
            return
        if self.critical:
            self.show_critical_warning()
            return
        self.button.setEnabled(False)
        self.stage.setText("Cancelando com segurança…" if self.pt else "Cancelling safely…")
        self.cancel_signal.write_text("cancel", encoding="ascii")
        self.log.appendPlainText(
            "Cancelamento solicitado. Aguardando a etapa atual terminar com segurança."
            if self.pt else "Cancellation requested. Waiting for the current step to finish safely."
        )

    def show_critical_warning(self):
        QMessageBox.warning(
            self,
            "GitBridge — ATENÇÃO" if self.pt else "GitBridge — WARNING",
            ("A restauração está em uma etapa crítica. NÃO FECHE A JANELA!\n\n"
             "O GitBridge está substituindo a pasta Anki2. Se você encerrar o processo "
             "à força ou desligar o computador agora, o Anki2 pode ficar ausente ou "
             "incompleto e exigir recuperação manual a partir da cópia de segurança.")
            if self.pt else
            ("Restore is at a critical step. DO NOT CLOSE THIS WINDOW!\n\n"
             "GitBridge is replacing the Anki2 folder. Forcefully terminating the process "
             "or shutting down the computer now may leave Anki2 missing or incomplete, "
             "requiring manual recovery from the preserved backup."),
        )

    def closeEvent(self, event):
        if not self.done:
            event.ignore()
            if self.restore and self.critical:
                self.show_critical_warning()
            else:
                self.showMinimized()
        else:
            event.accept()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", required=True)
    parser.add_argument("--anki-icon", default="")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--language", choices=("en", "pt-BR"), default="en")
    options, arguments = parser.parse_known_args()
    # The last two arguments are -Theme dark/light from the launcher.
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))
    window = BackupWindow(options.worker, arguments, options.anki_icon,
                          options.restore, options.language)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
