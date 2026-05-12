# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import (QAction, QMessageBox, QPushButton, QVBoxLayout, 
                                 QDialog, QLabel, QProgressBar, QHBoxLayout)
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QSettings, QUrl, QEventLoop
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply
from qgis.core import QgsProject, QgsMessageLog, Qgis, QgsApplication, QgsAuthMethodConfig, QgsNetworkAccessManager
import os
import tempfile
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
            
            # Para downloads síncronos em uma thread separada
            loop = QEventLoop()
            reply = manager.get(request)
            reply.finished.connect(loop.quit)
            
            # Conectar progresso
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
            "BAHIA": "https://drive.google.com/file/d/1IU37Bn7NvgdMd-t1GQ3y-yfA88Wm3R6z/view?usp=sharing"
        }
        
        self.geoserver_host = "geoservico.com.br"
        self.geoserver_base_url = f"http://{self.geoserver_host}:8086/geoserver/"
        self.geoserver_auth_url = f"{self.geoserver_base_url}wms?service=WMS&version=1.1.1&request=GetCapabilities"
        
        self.worker = None
        self.login_dlg = None
        self.state_dlg = None

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        self.action = QAction(QIcon(icon_path), "GeoNexus", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("GeoNexus", self.action)

    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        self.iface.removePluginMenu("GeoNexus", self.action)

    def run(self):
        self.show_login()

    def show_login(self):
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
        else:
            self.login_dlg.lineUser.setText("")
            self.login_dlg.linePass.setText("")
            self.login_dlg.saveCredentialsCheckBox.setChecked(False)

        def attempt_login():
            user = self.login_dlg.lineUser.text()
            pw = self.login_dlg.linePass.text()
            
            if not user or not pw:
                QMessageBox.warning(self.login_dlg, "Aviso", "Por favor, preencha o usuário e a senha.")
                return

            if self.validate_login(user, pw):
                if self.login_dlg.saveCredentialsCheckBox.isChecked():
                    self.settings.setValue("username", user)
                    self.settings.setValue("password", pw)
                else:
                    self.settings.remove("username")
                    self.settings.remove("password")
                
                # Armazenar credenciais no Gerenciador de Autenticação do QGIS
                # Isso silencia os pedidos de senha das camadas WMS/WFS
                self.setup_qgis_auth(user, pw)
                
                self.login_dlg.accept()
                self.show_state_selection()
            else:
                QMessageBox.critical(self.login_dlg, "Erro", "Usuário ou senha incorretos ou sem permissão de acesso.")

        try:
            self.login_dlg.buttonBox.accepted.disconnect()
        except Exception: pass
        
        self.login_dlg.buttonBox.accepted.connect(attempt_login)
        self.login_dlg.exec_()

    def setup_qgis_auth(self, user, pw):
        """Configura as credenciais no Gerenciador de Autenticação do QGIS de forma segura"""
        try:
            auth_mgr = QgsApplication.authManager()
            
            # Criar uma configuração de autenticação básica
            cfg = QgsAuthMethodConfig("Basic")
            cfg.setName(f"GeoNexus_{user}")
            cfg.setConfig("username", user)
            cfg.setConfig("password", pw)
            
            # Salvar no banco de dados de autenticação do QGIS
            auth_mgr.storeMethodConfig(cfg)
            
            # Injetar no cache de autenticação de rede para o domínio específico
            # Isso faz com que o QGIS use estas credenciais automaticamente para o GeoServer
            auth_mgr.updateNetworkProxy(cfg)
            
            QgsMessageLog.logMessage("Autenticação configurada com sucesso no QGIS.", "GeoNexus", Qgis.Info)
        except Exception as e:
            QgsMessageLog.logMessage(f"Erro ao configurar Gerenciador de Autenticação: {str(e)}", "GeoNexus", Qgis.Warning)

    def validate_login(self, user, pw):
        try:
            url = QUrl(self.geoserver_auth_url)
            manager = QgsNetworkAccessManager.instance()
            request = QNetworkRequest(url)
            
            # Configurar autenticação básica para a requisição
            import base64
            auth_str = f"{user}:{pw}"
            auth_bytes = auth_str.encode("ascii")
            base64_auth = base64.b64encode(auth_bytes).decode("ascii")
            request.setRawHeader(b"Authorization", f"Basic {base64_auth}".encode("ascii"))
            
            loop = QEventLoop()
            reply = manager.get(request)
            reply.finished.connect(loop.quit)
            loop.exec_()
            
            return reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) == 200
        except Exception as e:
            QgsMessageLog.logMessage(f"Erro de conexão com GeoServer: {str(e)}", "GeoNexus", Qgis.Critical)
            return False

    def show_state_selection(self):
        state_ui = os.path.join(self.plugin_dir, "form_state_selection.ui")
        self.state_dlg = uic.loadUi(state_ui)
        
        amapa_flag = os.path.join(self.plugin_dir, "flag_amapa.png")
        para_flag = os.path.join(self.plugin_dir, "flag_para.png")
        bahia_flag = os.path.join(self.plugin_dir, "flag_bahia.png")
        
        self.state_dlg.btnAmapa.setIcon(QIcon(amapa_flag))
        self.state_dlg.btnPara.setIcon(QIcon(para_flag))
        self.state_dlg.btnBahia.setIcon(QIcon(bahia_flag))
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet("""
            QProgressBar { border: 1px solid #bdc3c7; border-radius: 5px; text-align: center; background-color: #ecf0f1; }
            QProgressBar::chunk { background-color: #1a5a96; }
        """)
        self.progress_bar.hide()
        self.state_dlg.verticalLayout.insertWidget(self.state_dlg.verticalLayout.count() - 1, self.progress_bar)

        self.state_dlg.btnAmapa.clicked.connect(lambda: self.start_download("AMAPÁ"))
        self.state_dlg.btnPara.clicked.connect(lambda: self.start_download("PARÁ"))
        self.state_dlg.btnBahia.clicked.connect(lambda: self.start_download("BAHIA"))
        
        self.state_dlg.exec_()

    def start_download(self, state):
        url = self.urls[state]
        temp_dir = tempfile.gettempdir()
        dest_path = os.path.join(temp_dir, f"PROJETO_CAR_{state}.qgz")
        
        self.state_dlg.btnAmapa.setEnabled(False)
        self.state_dlg.btnPara.setEnabled(False)
        self.state_dlg.btnBahia.setEnabled(False)
        self.progress_bar.show()
        
        self.worker = DownloadWorker(url, dest_path)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.finished.connect(lambda s, r: self.on_finished(s, r, state))
        self.worker.start()

    def on_finished(self, success, result, state):
        if success:
            if QgsProject.instance().read(result):
                self.state_dlg.accept()
            else:
                QMessageBox.critical(self.state_dlg, "Erro", "Falha ao abrir o projeto.")
        else:
            QMessageBox.critical(self.state_dlg, "Erro", f"Falha no download: {result}")
        
        self.state_dlg.btnAmapa.setEnabled(True)
        self.state_dlg.btnPara.setEnabled(True)
        self.state_dlg.btnBahia.setEnabled(True)
        self.progress_bar.hide()
