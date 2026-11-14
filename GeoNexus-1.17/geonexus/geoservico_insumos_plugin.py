# -*- coding: utf-8 -*-
"""GeoNexus 1.17 — Acesso WMS com login unico."""
import json
import base64
import os
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote

from qgis.PyQt.QtCore import Qt, QSettings, QTimer, QUrl, QStandardPaths
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QPushButton, QProgressBar, QMessageBox, QCheckBox,
    QFrame, QScrollArea, QWidget
)
from qgis.core import QgsProject, QgsApplication, QgsAuthMethodConfig, QgsNetworkAccessManager, QgsMessageLog, Qgis

STATES = [
    ('AMAPÁ', 'flag_amapa.png'), ('PARÁ', 'flag_para.png'),
    ('BAHIA', 'flag_bahia.png'), ('GOIÁS', 'flag_goias.png'),
    ('TOCANTINS', 'flag_tocantins.png'), ('MATO GROSSO', 'flag_matogrosso.png'),
    ('RONDÔNIA', 'flag_rondonia.png'),
]
STYLE = """
QDialog {background:#f4f7fb; color:#17314c; font-size:12px;}
QLabel#title {font-size:21px; font-weight:700; color:#123956;}
QLabel#muted {color:#667b8f;}
QLineEdit {background:white; border:1px solid #cbd7e3; border-radius:7px; padding:9px;}
QLineEdit:focus {border:1px solid #267a9e;}
QPushButton {background:#145d79; color:white; border:0; border-radius:7px; padding:10px; font-weight:600;}
QPushButton:hover {background:#197a9d;}
QPushButton:disabled {background:#aebec8;}
QPushButton#state {background:white; color:#17314c; border:1px solid #d7e2eb; text-align:left; padding:13px;}
QPushButton#state:hover {border:1px solid #267a9e; background:#e8f5fa;}
QFrame#panel {background:white; border:1px solid #e0e8ef; border-radius:10px;}
QProgressBar {border:1px solid #d7e2eb; border-radius:5px; text-align:center; height:18px; background:white;}
QProgressBar::chunk {background:#218b94; border-radius:4px;}
"""


def drive_url(value):
    parsed = urlparse(value)
    if parsed.scheme != 'https' or parsed.hostname not in ('drive.google.com', 'docs.google.com'):
        raise ValueError('Origem do projeto não autorizada')
    match = re.search(r'/file/d/([A-Za-z0-9_-]+)', parsed.path)
    file_id = match.group(1) if match else parse_qs(parsed.query).get('id', [''])[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{10,}', file_id):
        raise ValueError('Identificador do arquivo inválido')
    return 'https://drive.google.com/uc?export=download&id=' + file_id


class GeoservicoInsumosPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.settings = QSettings('FORGEGEO', 'AnaliseCAR')
        # Purge plaintext credentials from 1.16 at initialization.
        self.settings.remove('password')
        self.urls = {
            "AMAPÁ": "https://drive.google.com/file/d/1EVLDIkH9XTdV3etJ1XH-H9dcpluOEJZ8/view?usp=sharing",
            "PARÁ": "https://drive.google.com/file/d/1ZcyIZDZ88zq3sLrOWThKOdy-Dkq5JTQU/view?usp=sharing",
            "BAHIA": "https://drive.google.com/file/d/1IU37Bn7NvgdMd-t1GQ3y-yfA88Wm3R6z/view?usp=sharing",
            "GOIÁS": "https://drive.google.com/file/d/1H9DFZNvWeCc6vpRaEG23ojNmWayTMNAP/view?usp=sharing",
            "TOCANTINS": "https://drive.google.com/file/d/1NOxL0_p4i4ZCWOupJGkqUB5JVVn4a7Hr/view?usp=sharing",
            "MATO GROSSO": "https://drive.google.com/file/d/1Xz5Te9DlYg2PToneaF6pUNl9Dfox5KnZ/view?usp=sharing",
            "RONDÔNIA": "https://drive.google.com/file/d/1jobL3KJ1AchDFgbJNDMJJs4xs_ohPJNc/view?usp=sharing"
        }
        
        # GeoServer REST: login HTTP Basic (sem API de assinatura separada).
        self.api_base_url = 'http://geoservico.com.br:8086/geoserver/forgegeo_pa/wms'
        self.settings.setValue('api_base_url', self.api_base_url)
        self._basic_authorization = None  # Mantido apenas em memória durante a sessão.
        self.machine_id = self.settings.value('machine_id', '') or str(uuid.uuid4())
        self.settings.setValue('machine_id', self.machine_id)
        self.current_user = None
        self.session_token = None
        self.auth_config_id = None
        self.manager = QgsNetworkAccessManager.instance()
        self.heartbeat_timer = QTimer()
        self.heartbeat_timer.timeout.connect(self.send_heartbeat)
        self.active = {}  # Keep QNetworkReply objects alive until completion
        self.dialog = None
        self.login_dialog = None
        self.download_reply = None
        self.download_file = None
        self.download_target = None
        self.downloading = False
        self._heartbeat_pending = False
        self._closed = False

    def initGui(self):
        self.action = QAction(QIcon(os.path.join(self.plugin_dir, 'icon.png')), 'GeoNexus', self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu('GeoNexus', self.action)

    def unload(self):
        self._closed = True
        self.heartbeat_timer.stop()
        self.cancel_download()
        self.logout_api()
        for reply in list(self.active):
            try:
                reply.abort()
            except RuntimeError:
                pass
        self.iface.removeToolBarIcon(self.action)
        self.iface.removePluginMenu('GeoNexus', self.action)

    def run(self):
        if self.session_token:
            self.show_state_selection()
        else:
            self.show_login()

    def _get_rest(self, callback, authorization=None, timeout_ms=15000):
        # Endpoint protegido por padrão; validar no servidor que acesso anônimo está desativado.
        url = QUrl(self.api_base_url + '?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities')
        req = QNetworkRequest(url)
        req.setRawHeader(b'Accept', b'application/xml')
        req.setRawHeader(b'Authorization', authorization or self._basic_authorization or b'')
        # Não encaminhar credenciais a outros hosts por redirecionamento.
        req.setAttribute(QNetworkRequest.FollowRedirectsAttribute, False)
        reply = self.manager.get(req)
        self.active[reply] = callback
        timer = QTimer(reply)
        timer.setSingleShot(True)
        timer.timeout.connect(reply.abort)
        timer.start(timeout_ms)

        def complete():
            timer.stop()
            cb = self.active.pop(reply, None)
            if cb is None:
                reply.deleteLater()
                return
            status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            error = reply.error()
            data = bytes(reply.readAll()).decode('utf-8', errors='replace')
            reply.deleteLater()
            if not self._closed:
                cb(status, error, data)
        reply.finished.connect(complete)
        return reply

    def show_login(self):
        dlg = QDialog(self.iface.mainWindow())
        self.login_dialog = dlg
        dlg.setWindowTitle('GeoNexus 1.17 | GeoServer WMS')
        dlg.setMinimumWidth(430)
        dlg.setStyleSheet(STYLE)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(12)
        logo = QLabel()
        pix = QPixmap(os.path.join(self.plugin_dir, 'logo_geonexus.png'))
        logo.setPixmap(pix.scaled(240, 95, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo.setAlignment(Qt.AlignCenter)
        lay.addWidget(logo)
        title = QLabel('Acesso ao GeoNexus')
        title.setObjectName('title')
        lay.addWidget(title)
        lay.addWidget(QLabel('Entre com seu usuário e senha do GeoServer.'))
        user = QLineEdit()
        user.setPlaceholderText('Usuário')
        user.setText(str(self.settings.value('username', '')))
        password = QLineEdit()
        password.setPlaceholderText('Senha')
        password.setEchoMode(QLineEdit.Password)
        lay.addWidget(user)
        lay.addWidget(password)
        remember = QCheckBox('Lembrar apenas o nome de usuário')
        remember.setChecked(bool(user.text()))
        lay.addWidget(remember)
        status_label = QLabel('')
        status_label.setWordWrap(True)
        status_label.setObjectName('muted')
        lay.addWidget(status_label)
        login_btn = QPushButton('Entrar')
        lay.addWidget(login_btn)
        cancel_btn = QPushButton('Cancelar')
        cancel_btn.clicked.connect(dlg.reject)
        lay.addWidget(cancel_btn)

        def attempt():
            username, pw = user.text().strip(), password.text()
            if not username or not pw:
                status_label.setText('Informe o usuário e a senha.')
                return
            login_btn.setEnabled(False)
            status_label.clear()  # Mantém somente mensagens de erro e validação

            authorization = b'Basic ' + base64.b64encode((username + ':' + pw).encode('utf-8'))
            def done(code, error, data):
                if code == 200:
                    # GetCapabilities pode ser público. Isto verifica a disponibilidade
                    # do WMS, mas NÃO comprova identidade nem assinatura ativa.
                    if '<WMS_Capabilities' not in data and '<WMT_MS_Capabilities' not in data:
                        status_label.setText('Resposta WMS inesperada; acesso não confirmado.')
                        login_btn.setEnabled(True)
                        return
                    try:
                        self.setup_qgis_auth(username, pw)
                    except Exception as exc:
                        status_label.setText('Falha no armazenamento protegido do QGIS: ' + str(exc))
                        QgsMessageLog.logMessage('Autenticacao local: {}'.format(exc), 'GeoNexus', Qgis.Warning)
                        login_btn.setEnabled(True)
                        return
                    self.current_user = username
                    self._basic_authorization = authorization
                    self.session_token = 'geoserver-wms-local-session'
                    if remember.isChecked():
                        self.settings.setValue('username', username)
                    else:
                        self.settings.remove('username')
                    self.settings.remove('password')
                    self.heartbeat_timer.start(30000)
                    dlg.accept()
                    return
                elif code == 401:
                    status_label.setText('Credenciais inválidas.')
                elif code == 403:
                    status_label.setText('Acesso ao WMS negado pelo GeoServer (HTTP 403).')
                elif code in (301, 302, 303, 307, 308):
                    status_label.setText('Redirecionamento inesperado (HTTP {}).'.format(code))
                elif code == 404:
                    status_label.setText('Serviço WMS não encontrado (HTTP 404).')
                else:
                    status_label.setText('Falha WMS (HTTP {}, erro {}).'.format(code or 'sem resposta', int(error)))
                login_btn.setEnabled(True)
            self._get_rest(done, authorization=authorization)
        login_btn.clicked.connect(attempt)
        password.returnPressed.connect(attempt)
        dlg.exec_()
        self.login_dialog = None
        if self.session_token and not self._closed:
            self.show_state_selection()

    def setup_qgis_auth(self, username, password):
        """Inicializa/desbloqueia o armazenamento QGIS via dialogo nativo.

        Nunca cria uma senha mestra silenciosa nem grava credenciais no projeto.
        A senha mestra do QGIS e distinta da senha do GeoServer.
        """
        self.auth_config_id = None
        manager = QgsApplication.authManager()
        if manager is None or manager.isDisabled():
            raise RuntimeError('Gerenciador de autenticacao indisponivel. Verifique a configuracao do QGIS.')

        # No QGIS Desktop, True solicita a criacao/desbloqueio da senha mestra
        # usando o dialogo nativo, quando necessario. Cancelar interrompe o login.
        if not manager.setMasterPassword(True):
            raise RuntimeError('Armazenamento protegido nao desbloqueado. Informe ou configure a senha mestra do QGIS.')

        # Reutilizar somente configuracao GeoNexus do mesmo usuario, sem
        # modificar autenticacoes de outros plugins/usuarios.
        cfg = None
        existing_id = str(self.settings.value('geonexus_authcfg_' + username, '') or '')
        if existing_id:
            existing = QgsAuthMethodConfig()
            if manager.loadAuthenticationConfig(existing_id, existing, True) and existing.method() == 'Basic' and existing.name() == 'GeoNexus_' + username:
                cfg = existing
        if cfg is None:
            cfg = QgsAuthMethodConfig('Basic')
            cfg.setName('GeoNexus_' + username)
        cfg.setConfig('username', username)
        cfg.setConfig('password', password)
        if cfg.id():
            ok = manager.updateAuthenticationConfig(cfg)
        else:
            ok = manager.storeAuthenticationConfig(cfg)
        if not ok or not cfg.id():
            raise RuntimeError('O QGIS nao conseguiu salvar a autenticacao HTTP Basic.')
        self.auth_config_id = cfg.id()
        self.settings.setValue('geonexus_authcfg_' + username, self.auth_config_id)

    @staticmethod
    def authenticated_wms_uri(uri, auth_id):
        """Preserve the QGIS WMS URI byte-for-byte except its auth parameters.

        The WMS provider expects `url` to remain the last datasource field.
        Do not urlencode the whole URI: this would change QGIS-specific fields
        such as `styles` and embedded URL query strings.
        """
        fields = uri.split('&')
        url_fields = [field for field in fields if field.split('=', 1)[0].lower() == 'url']
        if len(url_fields) != 1:
            return uri, False
        url = unquote(url_fields[0].partition('=')[2])
        parsed = urlparse(url)
        try:
            port = parsed.port
        except ValueError:
            return uri, False
        if (parsed.scheme != 'http' or parsed.hostname != 'geoservico.com.br'
                or port not in (8082, 8086)
                or not parsed.path.startswith('/geoserver/')):
            return uri, False
        if not re.fullmatch(r'[A-Za-z0-9]{7}', auth_id):
            raise ValueError('Identificador de autenticação QGIS inválido')
        fields = [field for field in fields
                  if field.split('=', 1)[0].lower() not in
                  ('authcfg', 'username', 'password', 'url')]
        # QGIS WMS URI: authentication must precede the URL field.
        fields.append('authcfg=' + auth_id)
        fields.append(url_fields[0])
        return '&'.join(fields), True

    def prepare_authenticated_project(self, source, destination):
        """Patch only eligible WMS datasource values in a copy of the QGZ."""
        if not self.auth_config_id:
            raise ValueError('Autenticacao QGIS nao configurada.')
        changed = 0
        with zipfile.ZipFile(source, 'r') as original, zipfile.ZipFile(destination, 'w') as target:
            for item in original.infolist():
                content = original.read(item.filename)
                if item.filename.lower().endswith('.qgs'):
                    root = ET.fromstring(content)
                    for layer in root.iter('maplayer'):
                        if (layer.findtext('provider') or '').lower() != 'wms':
                            continue
                        node = layer.find('datasource')
                        if node is None or not node.text:
                            continue
                        updated, eligible = self.authenticated_wms_uri(
                            node.text, self.auth_config_id)
                        if eligible:
                            node.text = updated
                            changed += 1
                    content = ET.tostring(root, encoding='utf-8', xml_declaration=True)
                target.writestr(item, content)
        if not changed:
            raise ValueError('Nenhuma camada WMS GeoServer elegivel foi encontrada no projeto.')
        return changed

    def send_heartbeat(self):
        # Revalidação de disponibilidade WMS; não comprova assinatura ativa.
        if not self.session_token or self._heartbeat_pending:
            return
        self._heartbeat_pending = True
        def done(code, error, data):
            self._heartbeat_pending = False
            if code in (401, 403):
                self.logout_api()
                QMessageBox.warning(self.iface.mainWindow(), 'GeoNexus', 'Acesso WMS negado. Faça login novamente.')
            elif code != 200:
                QgsMessageLog.logMessage('Não foi possível verificar o serviço WMS (HTTP {}).'.format(code), 'GeoNexus', Qgis.Warning)
        self._get_rest(done, timeout_ms=10000)

    def logout_api(self):
        # HTTP Basic não tem endpoint de logout: limpar credenciais em memória.
        self.heartbeat_timer.stop()
        self._basic_authorization = None
        self.session_token = None
        self.current_user = None

    def show_state_selection(self):
        if not self.session_token:
            return self.show_login()
        dlg = QDialog(self.iface.mainWindow())
        self.dialog = dlg
        dlg.setWindowTitle('GeoNexus 1.17 | Seleção de estados')
        dlg.resize(620, 630)
        dlg.setStyleSheet(STYLE)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)
        lay.addWidget(QLabel('Selecione um estado para baixar e abrir o projeto no QGIS.'))
        search = QLineEdit()
        search.setPlaceholderText('Pesquisar estado...')
        lay.addWidget(search)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        grid = QGridLayout(host)
        grid.setSpacing(9)
        buttons = []
        for idx, (state, flag) in enumerate(STATES):
            btn = QPushButton('  ' + state)
            btn.setObjectName('state')
            btn.setIcon(QIcon(os.path.join(self.plugin_dir, flag)))
            btn.setMinimumHeight(52)
            btn.clicked.connect(lambda checked=False, s=state: self.start_download(s))
            grid.addWidget(btn, idx // 2, idx % 2)
            buttons.append((state, btn))
        grid.setRowStretch(4, 1)
        scroll.setWidget(host)
        lay.addWidget(scroll, 1)
        search.textChanged.connect(lambda value: [btn.setVisible(value.casefold() in state.casefold()) for state, btn in buttons])
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.hide()
        lay.addWidget(self.progress_bar)
        self.status_label = QLabel('Pronto para selecionar um estado.')
        self.status_label.setObjectName('muted')
        lay.addWidget(self.status_label)
        actions = QHBoxLayout()
        self.cancel_btn = QPushButton('Cancelar download')
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_download)
        actions.addWidget(self.cancel_btn)
        close = QPushButton('Fechar')
        close.clicked.connect(dlg.reject)
        actions.addWidget(close)
        lay.addLayout(actions)
        dlg.finished.connect(lambda _: self.cancel_download())
        dlg.exec_()
        self.dialog = None

    def _set_downloading(self, enabled):
        self.downloading = enabled
        if self.dialog:
            self.cancel_btn.setEnabled(enabled)
            self.progress_bar.setVisible(enabled)

    def start_download(self, state):
        if not self.session_token:
            QMessageBox.warning(self.dialog, 'GeoNexus', 'Sessão não autenticada.')
            return
        if self.downloading:
            return
        try:
            url = drive_url(self.urls[state])
        except ValueError as exc:
            QMessageBox.critical(self.dialog, 'GeoNexus', str(exc))
            return
        base = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
        if not base:
            QMessageBox.critical(self.dialog, 'GeoNexus', 'Diretório local indisponível.')
            return
        folder = Path(base) / 'geonexus' / 'projects'
        folder.mkdir(parents=True, exist_ok=True)
        self.download_target = folder / ('PROJETO_CAR_' + state.replace(' ', '_') + '.qgz')
        temp = self.download_target.with_suffix('.qgz.part')
        self.download_file = open(str(temp), 'wb')
        self.progress_bar.setValue(0)
        self.status_label.setText('Baixando projeto: ' + state)
        self._set_downloading(True)
        request = QNetworkRequest(QUrl(url))
        reply = self.manager.get(request)
        self.download_reply = reply
        timeout = QTimer(reply)
        timeout.setSingleShot(True)
        timeout.timeout.connect(reply.abort)
        timeout.start(120000)

        def write_chunk():
            if self.download_file:
                self.download_file.write(bytes(reply.readAll()))
        def progress(received, total):
            if total > 0:
                self.progress_bar.setValue(min(100, int(received * 100 / total)))
            else:
                self.progress_bar.setFormat('Baixando...')
        def finish():
            timeout.stop()
            write_chunk()
            error = reply.error()
            reply.deleteLater()
            self.download_reply = None
            if self.download_file:
                self.download_file.close()
                self.download_file = None
            self._set_downloading(False)
            if error != QNetworkReply.NoError:
                temp.unlink(missing_ok=True)
                self.status_label.setText('Download interrompido ou indisponível.')
                return
            try:
                if temp.stat().st_size > 200 * 1024 * 1024:
                    raise ValueError('Projeto excede o limite de 200 MB')
                with zipfile.ZipFile(temp) as archive:
                    if archive.testzip() is not None or not any(n.endswith('.qgs') for n in archive.namelist()):
                        raise ValueError('O arquivo não é um projeto QGIS válido')
                authenticated = temp.with_suffix('.authenticated.qgz')
                try:
                    count = self.prepare_authenticated_project(temp, authenticated)
                    with zipfile.ZipFile(authenticated) as archive:
                        if archive.testzip() is not None:
                            raise ValueError('Projeto autenticado corrompido')
                    os.replace(str(authenticated), str(self.download_target))
                finally:
                    temp.unlink(missing_ok=True)
                    authenticated.unlink(missing_ok=True)
                if not QgsProject.instance().read(str(self.download_target)):
                    raise ValueError('O QGIS não conseguiu abrir o projeto')
                QgsMessageLog.logMessage('Projeto com {} camadas WMS autenticadas.'.format(count), 'GeoNexus', Qgis.Info)
            except (OSError, zipfile.BadZipFile, ValueError) as exc:
                temp.unlink(missing_ok=True)
                self.status_label.setText('Falha: ' + str(exc))
                return
            self.status_label.setText('Projeto carregado: ' + state)
            self.dialog.accept()
        reply.readyRead.connect(write_chunk)
        reply.downloadProgress.connect(progress)
        reply.finished.connect(finish)

    def cancel_download(self):
        if self.download_reply is not None:
            self.download_reply.abort()
