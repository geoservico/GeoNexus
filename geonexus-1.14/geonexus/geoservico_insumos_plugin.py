# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import (QAction, QMessageBox, QPushButton, QVBoxLayout, 
                                 QDialog, QLabel, QProgressBar, QHBoxLayout)
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QSettings, QUrl, QEventLoop
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply
from qgis.core import QgsProject, QgsMessageLog, Qgis, QgsApplication, QgsAuthMethodConfig, QgsNetworkAccessManager
import os
import tempfile
import subprocess
import platform
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
            "BAHIA": "https://drive.google.com/file/d/1IU37Bn7NvgdMd-t1GQ3y-yfA88Wm3R6z/view?usp=sharing",
            "GOIÁS": "https://drive.google.com/file/d/1H9DFZNvWeCc6vpRaEG23ojNmWayTMNAP/view?usp=sharing",
            "TOCANTINS": "https://drive.google.com/file/d/1NOxL0_p4i4ZCWOupJGkqUB5JVVn4a7Hr/view?usp=sharing",
            "MATO GROSSO": "https://drive.google.com/file/d/1Xz5Te9DlYg2PToneaF6pUNl9Dfox5KnZ/view?usp=sharing",
            "RONDÔNIA": "https://drive.google.com/file/d/1jobL3KJ1AchDFgbJNDMJJs4xs_ohPJNc/view?usp=sharing"
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

    def open_link_with_preferred_browser(self, url):
        """Abre um link preferencialmente no Chrome ou Firefox, em último caso no Edge"""
        system = platform.system()
        browsers = []
        
        if system == "Windows":
            # Windows: Chrome, Firefox, Edge
            browsers = [
                "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
                "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
                "C:\\Program Files\\Mozilla Firefox\\firefox.exe",
                "C:\\Program Files (x86)\\Mozilla Firefox\\firefox.exe",
                "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe"
            ]
        elif system == "Darwin":  # macOS
            browsers = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Firefox.app/Contents/MacOS/firefox",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
            ]
        else:  # Linux
            browsers = ["google-chrome", "chromium-browser", "chromium", "firefox", "microsoft-edge"]
        
        # Tentar abrir com cada navegador até conseguir
        for browser in browsers:
            try:
                if system == "Windows" or system == "Darwin":
                    subprocess.Popen([browser, url])
                else:  # Linux
                    subprocess.Popen([browser, url])
                return True
            except (FileNotFoundError, OSError):
                continue
        
        # Se nenhum navegador preferencial foi encontrado, usar o padrão do sistema
        try:
            import webbrowser
            webbrowser.open(url)
            return True
        except Exception:
            return False
    
    def show_login(self):
        login_ui = os.path.join(self.plugin_dir, "form_login.ui")
        self.login_dlg = uic.loadUi(login_ui)
        self.login_dlg.setWindowTitle("GeoNexus - Login")
        
        logo_path = os.path.join(self.plugin_dir, "logo_geonexus.png")
        if hasattr(self.login_dlg, 'logoLabel'):
            self.login_dlg.logoLabel.setPixmap(QPixmap(logo_path))
        
        # Desabilitar abertura automática de links e conectar ao handler customizado
        if hasattr(self.login_dlg, 'subscribeLabel'):
            self.login_dlg.subscribeLabel.setOpenExternalLinks(False)
            self.login_dlg.subscribeLabel.linkActivated.connect(
                lambda url: self.open_link_with_preferred_browser(url)
            )
        
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
        try:
            auth_mgr = QgsApplication.authManager()
            cfg = QgsAuthMethodConfig("Basic")
            cfg.setName(f"GeoNexus_{user}")
            cfg.setConfig("username", user)
            cfg.setConfig("password", pw)
            auth_mgr.storeMethodConfig(cfg)
            auth_mgr.updateNetworkProxy(cfg)
            QgsMessageLog.logMessage("Autenticação configurada com sucesso no QGIS.", "GeoNexus", Qgis.Info)
        except Exception as e:
            QgsMessageLog.logMessage(f"Erro ao configurar Gerenciador de Autenticação: {str(e)}", "GeoNexus", Qgis.Warning)

    def validate_login(self, user, pw):
        try:
            url = QUrl(self.geoserver_auth_url)
            manager = QgsNetworkAccessManager.instance()
            request = QNetworkRequest(url)
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
        
        flags = {
            "AMAPÁ": "flag_amapa.png",
            "PARÁ": "flag_para.png",
            "BAHIA": "flag_bahia.png",
            "GOIÁS": "flag_goias.png",
            "TOCANTINS": "flag_tocantins.png",
            "MATO GROSSO": "flag_matogrosso.png",
            "RONDÔNIA": "flag_rondonia.png"
        }
        
        buttons = {
            "AMAPÁ": self.state_dlg.btnAmapa,
            "PARÁ": self.state_dlg.btnPara,
            "BAHIA": self.state_dlg.btnBahia,
            "GOIÁS": self.state_dlg.btnGoias,
            "TOCANTINS": self.state_dlg.btnTocantins,
            "MATO GROSSO": self.state_dlg.btnMatoGrosso,
            "RONDÔNIA": self.state_dlg.btnRondonia
        }
        
        labels = {
            "AMAPÁ": self.state_dlg.labelAmapa,
            "PARÁ": self.state_dlg.labelPara,
            "BAHIA": self.state_dlg.labelBahia,
            "GOIÁS": self.state_dlg.labelGoias,
            "TOCANTINS": self.state_dlg.labelTocantins,
            "MATO GROSSO": self.state_dlg.labelMatoGrosso,
            "RONDÔNIA": self.state_dlg.labelRondonia
        }
        
        for state, btn in buttons.items():
            flag_path = os.path.join(self.plugin_dir, flags[state])
            btn.setIcon(QIcon(flag_path))
            btn.clicked.connect(lambda checked, s=state: self.start_download(s))
        
        # Configurar campo de busca
        search_line = self.state_dlg.searchLineEdit
        search_line.textChanged.connect(lambda text: self.filter_states(text, buttons, labels))
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet("""
            QProgressBar { border: 1px solid #bdc3c7; border-radius: 5px; text-align: center; background-color: #ecf0f1; }
            QProgressBar::chunk { background-color: #1a5a96; }
        """)
        self.progress_bar.hide()
        self.state_dlg.verticalLayout.insertWidget(self.state_dlg.verticalLayout.count() - 1, self.progress_bar)
        self.state_dlg.exec_()

    def filter_states(self, search_text, buttons, labels):
        """Filtra os estados baseado no texto de busca"""
        search_text = search_text.lower().strip()
        
        # Mapeamento de alternativas de busca
        search_aliases = {
            "amapá": ["amapá", "amapa"],
            "pará": ["pará", "para"],
            "bahia": ["bahia"],
            "goiás": ["goiás", "goias"],
            "tocantins": ["tocantins"],
            "mato grosso": ["mato grosso", "matogrosso", "mato"],
            "rondônia": ["rondônia", "rondonia"]
        }
        
        visible_count = 0
        
        for state, btn in buttons.items():
            label = labels[state]
            
            # Se o campo de busca está vazio, mostrar todos
            if not search_text:
                btn.setVisible(True)
                label.setVisible(True)
                visible_count += 1
            else:
                # Verificar se o estado corresponde à busca
                state_lower = state.lower()
                is_match = False
                
                # Busca direta no nome do estado
                if search_text in state_lower:
                    is_match = True
                else:
                    # Busca nos aliases
                    for key, aliases in search_aliases.items():
                        if key.lower() == state_lower:
                            for alias in aliases:
                                if search_text in alias:
                                    is_match = True
                                    break
                        if is_match:
                            break
                
                btn.setVisible(is_match)
                label.setVisible(is_match)
                if is_match:
                    visible_count += 1
        
        # Mostrar mensagem de "nenhum resultado" se necessário
        no_results_label = self.state_dlg.noResultsLabel
        if search_text and visible_count == 0:
            no_results_label.setVisible(True)
        else:
            no_results_label.setVisible(False)

    def start_download(self, state):
        url = self.urls[state]
        temp_dir = tempfile.gettempdir()
        dest_path = os.path.join(temp_dir, f"PROJETO_CAR_{state.replace(' ', '_')}.qgz")
        
        for btn in [self.state_dlg.btnAmapa, self.state_dlg.btnPara, self.state_dlg.btnBahia, 
                    self.state_dlg.btnGoias, self.state_dlg.btnTocantins, self.state_dlg.btnMatoGrosso,
                    self.state_dlg.btnRondonia]:
            btn.setEnabled(False)
            
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
        
        for btn in [self.state_dlg.btnAmapa, self.state_dlg.btnPara, self.state_dlg.btnBahia, 
                    self.state_dlg.btnGoias, self.state_dlg.btnTocantins, self.state_dlg.btnMatoGrosso,
                    self.state_dlg.btnRondonia]:
            btn.setEnabled(True)
        self.progress_bar.hide()
