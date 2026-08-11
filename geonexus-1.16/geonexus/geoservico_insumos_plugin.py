# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import (QAction, QMessageBox, QPushButton, QVBoxLayout, 
                                 QDialog, QLabel, QProgressBar, QHBoxLayout)
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QSettings, QUrl, QEventLoop, QTimer
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply
from qgis.core import QgsProject, QgsMessageLog, Qgis, QgsApplication, QgsAuthMethodConfig, QgsNetworkAccessManager
import os
import tempfile
import subprocess
import platform
import uuid
import json
from PyQt5 import uic

class DownloadWorker(QThread):
    """Worker thread para baixar o projeto do Google Drive"""
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)
    
    def __init__(self, url, dest_path):
        super().__init__()
        self.url = url
        self.dest_path = dest_path
        
    def run(self):
        try:
            if "/file/d/" in self.url:
                file_id = self.url.split("/file/d/")[1].split("/")[0]
            else:
                file_id = self.url.split("id=")[1].split("&")[0]
                
            direct_link = f"https://drive.google.com/uc?export=download&id={file_id}"
            
            manager = QgsNetworkAccessManager.instance()
            request = QNetworkRequest(QUrl(direct_link))
            
            loop = QEventLoop()
            reply = manager.get(request)
            reply.finished.connect(loop.quit)
            
            def update_progress(received, total):
                if total > 0:
                    self.progress.emit(int(100 * received / total))
            
            reply.downloadProgress.connect(update_progress)
            loop.exec_()
            
            if reply.error() != QNetworkReply.NoError:
                self.finished.emit(False, f"Erro ao baixar o arquivo: {reply.errorString()}")
                return

            content = reply.readAll()
            with open(self.dest_path, "wb") as f:
                f.write(content.data())
            
            self.finished.emit(True, self.dest_path)
        except Exception as e:
            self.finished.emit(False, str(e))

class GeoservicoInsumosPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.settings = QSettings("FORGEGEO", "AnaliseCAR")
        
        self.urls = {
            "AMAPÁ": "https://drive.google.com/file/d/1EVLDIkH9XTdV3etJ1XH-H9dcpluOEJZ8/view?usp=sharing",
            "PARÁ": "https://drive.google.com/file/d/1ZcyIZDZ88zq3sLrOWThKOdy-Dkq5JTQU/view?usp=sharing",
            "BAHIA": "https://drive.google.com/file/d/1IU37Bn7NvgdMd-t1GQ3y-yfA88Wm3R6z/view?usp=sharing",
            "GOIÁS": "https://drive.google.com/file/d/1H9DFZNvWeCc6vpRaEG23ojNmWayTMNAP/view?usp=sharing",
            "TOCANTINS": "https://drive.google.com/file/d/1NOxL0_p4i4ZCWOupJGkqUB5JVVn4a7Hr/view?usp=sharing",
            "MATO GROSSO": "https://drive.google.com/file/d/1Xz5Te9DlYg2PToneaF6pUNl9Dfox5KnZ/view?usp=sharing",
            "RONDÔNIA": "https://drive.google.com/file/d/1jobL3KJ1AchDFgbJNDMJJs4xs_ohPJNc/view?usp=sharing"
        }
        
        # --- Configurações da API na VPS ---
        # TODO: O usuário deve substituir pelo IP/Domínio real da VPS
        self.api_base_url = "http://geoservico.com.br:8001" 
        
        self.current_user = None
        self.session_token = None
        self.machine_id = self.get_machine_id()
        
        self.heartbeat_timer = QTimer()
        self.heartbeat_timer.timeout.connect(self.send_heartbeat)
        
        self.worker = None
        self.login_dlg = None
        self.state_dlg = None
        self.loading_timer = None
        self.loading_frame = 0

    def get_machine_id(self):
        """Gera um ID único para a máquina baseado no hardware ou settings"""
        m_id = self.settings.value("machine_id", "")
        if not m_id:
            m_id = str(uuid.uuid4())
            self.settings.setValue("machine_id", m_id)
        return m_id

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        self.action = QAction(QIcon(icon_path), "GeoNexus", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("GeoNexus", self.action)

    def unload(self):
        self.logout_api()
        self.iface.removeToolBarIcon(self.action)
        self.iface.removePluginMenu("GeoNexus", self.action)

    def run(self):
        self.show_login()

    def _get_loading_label(self):
        """Obtém o indicador pertencente exclusivamente ao diálogo atual."""
        if self.login_dlg is None:
            return None
        try:
            return self.login_dlg.findChild(QLabel, "loadingLabel")
        except RuntimeError:
            return None

    def _set_login_loading(self, loading):
        """Exibe ou oculta o indicador animado durante o carregamento após OK."""
        if self.login_dlg is None:
            return

        try:
            label = self._get_loading_label()
            if loading:
                if label is None:
                    label = QLabel(self.login_dlg)
                    label.setObjectName("loadingLabel")
                    label.setAlignment(Qt.AlignCenter)
                    label.setStyleSheet(
                        "color: #1a5a96; font-size: 13px; font-weight: bold; padding: 6px;"
                    )
                    layout = self.login_dlg.verticalLayout
                    layout.insertWidget(layout.indexOf(self.login_dlg.buttonBox), label)

                self.loading_frame = 0
                label.setText("◴  Carregando aplicação.... Aguarde.")
                label.show()
                self.login_dlg.buttonBox.setEnabled(False)

                if self.loading_timer is None:
                    self.loading_timer = QTimer(self.login_dlg)
                    self.loading_timer.timeout.connect(self._animate_login_loading)
                self.loading_timer.start(180)
            else:
                if self.loading_timer is not None:
                    self.loading_timer.stop()
                if label is not None:
                    label.hide()
                self.login_dlg.buttonBox.setEnabled(True)
        except RuntimeError:
            # O diálogo pode ter sido destruído pelo QGIS entre dois eventos.
            # Descartar as referências permite que a próxima abertura comece limpa.
            self.loading_timer = None
            self.loading_frame = 0

    def _animate_login_loading(self):
        """Atualiza o símbolo do relógio no diálogo de login atualmente aberto."""
        label = self._get_loading_label()
        if label is None:
            return
        try:
            clock_frames = ("◴", "◷", "◶", "◵")
            frame = clock_frames[self.loading_frame % len(clock_frames)]
            label.setText(f"{frame}  Carregando aplicação.... Aguarde.")
            self.loading_frame += 1
        except RuntimeError:
            self.loading_timer = None

    def show_login(self):
        # O QDialog anterior pode ter destruído o QLabel e o QTimer associados.
        # Limpar as referências evita reutilizar wrappers C++ inválidos no QGIS.
        if self.loading_timer is not None:
            try:
                self.loading_timer.stop()
            except RuntimeError:
                pass
        self.loading_timer = None
        self.loading_frame = 0

        login_ui = os.path.join(self.plugin_dir, "form_login.ui")
        self.login_dlg = uic.loadUi(login_ui)
        self.login_dlg.setWindowTitle("GeoNexus - Login")
        
        logo_path = os.path.join(self.plugin_dir, "logo_geonexus.png")
        if hasattr(self.login_dlg, 'logoLabel'):
            self.login_dlg.logoLabel.setPixmap(QPixmap(logo_path))
        
        saved_user = self.settings.value("username", "")
        saved_pass = self.settings.value("password", "")
        
        if saved_user:
            self.login_dlg.lineUser.setText(saved_user)
            self.login_dlg.linePass.setText(saved_pass)
            self.login_dlg.saveCredentialsCheckBox.setChecked(True)

        def attempt_login():
            user = self.login_dlg.lineUser.text()
            pw = self.login_dlg.linePass.text()
            
            if not user or not pw:
                QMessageBox.warning(self.login_dlg, "Aviso", "Por favor, preencha o usuário e a senha.")
                return

            self._set_login_loading(True)
            success, data = self.login_via_api(user, pw)
            if success:
                self.current_user = user
                self.session_token = data.get("token")
                
                if self.login_dlg.saveCredentialsCheckBox.isChecked():
                    self.settings.setValue("username", user)
                    self.settings.setValue("password", pw)
                else:
                    self.settings.remove("username")
                    self.settings.remove("password")
                
                self.setup_qgis_auth(user, pw)
                self.heartbeat_timer.start(30000) # Heartbeat a cada 30s
                self._set_login_loading(False)
                self.login_dlg.accept()
                self.show_state_selection()
            else:
                self._set_login_loading(False)
                QMessageBox.critical(self.login_dlg, "Erro", data)

        try:
            self.login_dlg.buttonBox.accepted.disconnect()
        except (TypeError, RuntimeError):
            # Ignora se o sinal não estiver conectado
            pass
        
        self.login_dlg.buttonBox.accepted.connect(attempt_login)
        try:
            self.login_dlg.exec_()
        finally:
            # Não manter wrappers Python para objetos Qt que o QDialog destruiu.
            if self.loading_timer is not None:
                try:
                    self.loading_timer.stop()
                except RuntimeError:
                    pass
            self.loading_timer = None
            self.login_dlg = None

    def login_via_api(self, user, pw):
        """Realiza login através da API na VPS"""
        try:
            url = f"{self.api_base_url}/login"
            payload = {
                "username": user,
                "password": pw,
                "machine_id": self.machine_id
            }
            
            manager = QgsNetworkAccessManager.instance()
            request = QNetworkRequest(QUrl(url))
            request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
            
            loop = QEventLoop()
            reply = manager.post(request, json.dumps(payload).encode("utf-8"))
            reply.finished.connect(loop.quit)
            loop.exec_()
            
            status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            response_data = reply.readAll().data().decode("utf-8")
            
            if status == 200:
                return True, json.loads(response_data)
            elif status == 403:
                return False, "Acesso negado: Este usuário já está logado em outra máquina."
            else:
                return False, f"Erro de autenticação ({status}): {response_data}"
        except Exception as e:
            return False, f"Erro de conexão com a API: {str(e)}"

    def send_heartbeat(self):
        """Envia sinal de vida para a API"""
        if not self.session_token: return
        
        url = f"{self.api_base_url}/heartbeat"
        payload = {"username": self.current_user, "token": self.session_token}
        
        manager = QgsNetworkAccessManager.instance()
        request = QNetworkRequest(QUrl(url))
        request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        
        reply = manager.post(request, json.dumps(payload).encode("utf-8"))
        
        def handle_reply():
            status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            if status != 200:
                QgsMessageLog.logMessage("Sessão expirada ou invalidada remotamente.", "GeoNexus", Qgis.Warning)
                self.heartbeat_timer.stop()
                QMessageBox.warning(None, "Sessão Encerrada", "Sua sessão foi encerrada por acesso simultâneo ou tempo de inatividade.")
                # Opcional: fechar o QGIS ou desabilitar o plugin
            reply.deleteLater()
            
        reply.finished.connect(handle_reply)

    def logout_api(self):
        """Informa a API que o usuário está saindo"""
        if not self.session_token: return
        
        url = f"{self.api_base_url}/logout"
        payload = {"username": self.current_user, "token": self.session_token}
        
        manager = QgsNetworkAccessManager.instance()
        request = QNetworkRequest(QUrl(url))
        request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        manager.post(request, json.dumps(payload).encode("utf-8"))
        
        self.heartbeat_timer.stop()
        self.session_token = None

    def setup_qgis_auth(self, user, pw):
        try:
            auth_mgr = QgsApplication.authManager()
            cfg = QgsAuthMethodConfig("Basic")
            cfg.setName(f"GeoNexus_{user}")
            cfg.setConfig("username", user)
            cfg.setConfig("password", pw)
            auth_mgr.storeMethodConfig(cfg)
            auth_mgr.updateNetworkProxy(cfg)
        except Exception as e:
            QgsMessageLog.logMessage(f"Erro ao configurar Auth: {str(e)}", "GeoNexus", Qgis.Warning)

    def show_state_selection(self):
        state_ui = os.path.join(self.plugin_dir, "form_state_selection.ui")
        self.state_dlg = uic.loadUi(state_ui)
        
        flags = {
            "AMAPÁ": "flag_amapa.png", "PARÁ": "flag_para.png", "BAHIA": "flag_bahia.png",
            "GOIÁS": "flag_goias.png", "TOCANTINS": "flag_tocantins.png", 
            "MATO GROSSO": "flag_matogrosso.png", "RONDÔNIA": "flag_rondonia.png"
        }
        
        buttons = {
            "AMAPÁ": self.state_dlg.btnAmapa, "PARÁ": self.state_dlg.btnPara, "BAHIA": self.state_dlg.btnBahia,
            "GOIÁS": self.state_dlg.btnGoias, "TOCANTINS": self.state_dlg.btnTocantins,
            "MATO GROSSO": self.state_dlg.btnMatoGrosso, "RONDÔNIA": self.state_dlg.btnRondonia
        }
        
        for state, btn in buttons.items():
            flag_path = os.path.join(self.plugin_dir, flags[state])
            btn.setIcon(QIcon(flag_path))
            btn.clicked.connect(lambda checked, s=state: self.start_download(s))
        
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        self.state_dlg.verticalLayout.insertWidget(self.state_dlg.verticalLayout.count() - 1, self.progress_bar)
        self.state_dlg.exec_()

    def start_download(self, state):
        url = self.urls[state]
        temp_dir = tempfile.gettempdir()
        dest_path = os.path.join(temp_dir, f"PROJETO_CAR_{state.replace(' ', '_')}.qgz")
        
        self.progress_bar.show()
        self.worker = DownloadWorker(url, dest_path)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.finished.connect(lambda s, r: self.on_finished(s, r, state))
        self.worker.start()

    def on_finished(self, success, result, state):
        if success:
            QgsProject.instance().read(result)
            self.state_dlg.accept()
        else:
            QMessageBox.critical(self.state_dlg, "Erro", f"Falha no download: {result}")
        self.progress_bar.hide()
