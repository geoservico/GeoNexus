# -*- coding: utf-8 -*-
"""GeoNexus 1.17.3 — Acesso WMS com login unico."""
import json
import base64
import os
import re
import uuid
import zipfile
from defusedxml import ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote, urlencode

from qgis.PyQt.QtCore import Qt, QSettings, QTimer, QUrl, QStandardPaths
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QPushButton, QProgressBar, QMessageBox, QCheckBox,
    QFrame, QScrollArea, QWidget, QListWidget
)
from qgis.core import QgsProject, QgsApplication, QgsAuthMethodConfig, QgsNetworkAccessManager, QgsMessageLog, Qgis, QgsVectorLayer, QgsCoordinateTransform, QgsFillSymbol

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
        self.car_pa_url = "https://geoserverdw.apps.geoapplications.net/geoserver/workspace_fiscalizacao_inteligente/ows"
        self.car_pa_layer = "workspace_fiscalizacao_inteligente:vw_imoveis"

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
        req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
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
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
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
        dlg.setWindowTitle('GeoNexus 1.17.3 | GeoServer WMS')
        dlg.setMinimumWidth(430)
        dlg.setStyleSheet(STYLE)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(12)
        logo = QLabel()
        pix = QPixmap(os.path.join(self.plugin_dir, 'logo_geonexus.png'))
        logo.setPixmap(pix.scaled(240, 95, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        password.setEchoMode(QLineEdit.EchoMode.Password)
        lay.addWidget(user)
        lay.addWidget(password)
        save_credentials = QCheckBox('Salvar usuário e senha neste computador')
        save_credentials.setChecked(str(self.settings.value('geonexus_save_credentials', 'false')).lower() == 'true')
        lay.addWidget(save_credentials)

        def restore_credentials():
            if not save_credentials.isChecked():
                return
            username = user.text().strip()
            if not username:
                return
            try:
                manager = QgsApplication.authManager()
                if manager is None or manager.isDisabled() or not manager.setMasterPassword(True):
                    return
                config_id = str(self.settings.value('geonexus_authcfg_' + username, '') or '')
                cfg = QgsAuthMethodConfig()
                if (config_id and manager.loadAuthenticationConfig(config_id, cfg, True)
                        and cfg.method() == 'Basic'
                        and cfg.name() == 'GeoNexus_' + username
                        and cfg.config('username') == username):
                    password.setText(cfg.config('password') or '')
            except Exception as exc:
                QgsMessageLog.logMessage('Credenciais salvas indisponíveis: {}'.format(exc), 'GeoNexus', Qgis.MessageLevel.Warning)

        if save_credentials.isChecked():
            restore_credentials()
        user.editingFinished.connect(restore_credentials)
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
                        QgsMessageLog.logMessage('Autenticacao local: {}'.format(exc), 'GeoNexus', Qgis.MessageLevel.Warning)
                        login_btn.setEnabled(True)
                        return
                    self.current_user = username
                    self._basic_authorization = authorization
                    self.session_token = True
                    if save_credentials.isChecked():
                        self.settings.setValue('username', username)
                    else:
                        self.settings.remove('username')
                    self.settings.remove('password')
                    self.settings.setValue('geonexus_save_credentials', 'true' if save_credentials.isChecked() else 'false')
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
        dlg.exec()
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
                QgsMessageLog.logMessage('Não foi possível verificar o serviço WMS (HTTP {}).'.format(code), 'GeoNexus', Qgis.MessageLevel.Warning)
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
        dlg.setWindowTitle('GeoNexus 1.17.3 | Seleção de estados')
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
        scroll.setFrameShape(QFrame.Shape.NoFrame)
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
        # Consulta CAR apenas para o estado do Para; os demais estados nao sao alterados.
        pa_lookup = QPushButton('Consultar imóvel CAR — Pará')
        pa_lookup.clicked.connect(self.show_car_pa_search)
        lay.addWidget(pa_lookup)
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
        dlg.exec()
        self.dialog = None

    def _pa_request(self, params, callback):
        """Consulta OWS assíncrona sem credenciais do GeoServer comercial."""
        url = QUrl(self.car_pa_url + '?' + urlencode(params))
        req = QNetworkRequest(url)
        req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        reply = self.manager.get(req)
        self.active[reply] = callback
        timer = QTimer(reply)
        timer.setSingleShot(True)
        timer.timeout.connect(reply.abort)
        timer.start(20000)

        def finished():
            timer.stop()
            cb = self.active.pop(reply, None)
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            data = bytes(reply.readAll())
            error = reply.error()
            reply.deleteLater()
            if cb and not self._closed:
                cb(status, error, data)
        reply.finished.connect(finished)

    @staticmethod
    def _pa_field_match(fields, candidates):
        def normalized(value):
            return re.sub(r'[^a-z0-9]', '', value.lower())
        for candidate in candidates:
            for field in fields:
                if normalized(field) == normalized(candidate):
                    return field
        return None

    def show_car_pa_search(self):
        """Pesquisa limitada por código CAR ou CPF/CNPJ na vw_imoveis."""
        if not self.session_token:
            return self.show_login()
        dlg = QDialog(self.iface.mainWindow())
        dlg.setWindowTitle('GeoNexus 1.17.3 | Consulta CAR — Pará')
        dlg.setStyleSheet(STYLE)
        dlg.resize(590, 440)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel('Consultar imóvel CAR — Pará'))
        query = QLineEdit()
        query.setPlaceholderText('Código do imóvel CAR ou CPF/CNPJ')
        layout.addWidget(query)
        search = QPushButton('Buscar imóvel')
        layout.addWidget(search)
        status = QLabel('Informe o código CAR ou CPF/CNPJ.')
        status.setWordWrap(True)
        layout.addWidget(status)
        results = QListWidget()
        layout.addWidget(results)
        load = QPushButton('Carregar imóvel selecionado no QGIS')
        load.setEnabled(False)
        layout.addWidget(load)
        found = []
        schema = {'fields': None}

        def mask_name(value):
            # Exibe apenas a primeira letra de cada palavra, sem revelar o nome completo.
            return ' '.join((part[:1] + '*' * max(3, len(part) - 1)) for part in str(value or '').split()) or 'Nome não informado'

        def mask_document(value):
            digits = re.sub(r'\D', '', str(value or ''))
            if len(digits) == 11:
                return '***.***.***-' + digits[-2:]
            if len(digits) == 14:
                return '**.***.***/****-' + digits[-2:]
            return ''

        def protocol_key(feature):
            value = (feature.get('properties') or {}).get('dat_protocolo')
            if not value:
                return ''
            # Datas ISO 8601 do serviço (UTC) são comparáveis após normalização.
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).isoformat()
            except (ValueError, TypeError, OverflowError):
                return ''

        def done_features(http, error, payload):
            search.setEnabled(True)
            if http != 200:
                status.setText('Falha na consulta WFS (HTTP {}).'.format(http or 'sem resposta'))
                return
            if len(payload) > 15 * 1024 * 1024:
                status.setText('Resposta excessivamente grande. Refine a pesquisa.')
                return
            try:
                obj = json.loads(payload.decode('utf-8'))
                if obj.get('type') != 'FeatureCollection':
                    raise ValueError('Resposta não é GeoJSON')
                features = obj.get('features', [])
                if not isinstance(features, list):
                    raise ValueError('Lista de feições inválida')
                valid = [f for f in features if isinstance(f, dict) and f.get('geometry')]
                # Para cada código CAR, manter somente o protocolo mais recente.
                # Empates preservam o primeiro registro; outros imóveis permanecem na lista.
                newest = {}
                for feature in valid:
                    props = feature.get('properties') or {}
                    car_code = str(props.get('tx_cod_imovel') or '').strip().upper()
                    key = car_code or str(feature.get('id') or id(feature))
                    if key not in newest or protocol_key(feature) > protocol_key(newest[key]):
                        newest[key] = feature
                found[:] = sorted(newest.values(), key=protocol_key, reverse=True)
                results.clear()
                for i, f in enumerate(found):
                    props = f.get('properties') or {}
                    code = next((str(v) for k,v in props.items() if k.lower() in ('tx_cod_imovel','cod_imovel','codigo_imovel','cod_car','codigo_car','numero_car') and v), '')
                    name = next((str(v).strip() for k,v in props.items() if k.lower() == 'tx_nome_imovel' and v), '')
                    municipality = next((str(v).strip() for k,v in props.items() if k.lower() == 'tx_nome_municipio' and v), '')
                    status_value = next((str(v).strip() for k,v in props.items() if k.lower() == 'tx_status_imovel' and v), '')
                    area = next((v for k,v in props.items() if k.lower() == 'num_area_imovel' and v is not None), None)
                    details = [municipality, '{} ha'.format(area) if area is not None else '', status_value]
                    description = ' | '.join(item for item in details if item)
                    label = '{} — {}'.format(i + 1, name or 'Imóvel sem nome informado')
                    if description:
                        label += ' — ' + description
                    doc = next((mask_document(v) for k, v in props.items()
                                if k.lower() in ('tx_cpf_cnpj_proprietario', 'tx_cpf_cadastrante', 'tx_cnpj_cadastrante')
                                and mask_document(v)), '')
                    if doc:
                        label += ' — Documento: ' + doc
                    if code:
                        label += ' — CAR: ' + code
                    results.addItem(label)
                status.setText('{} imóvel(is) com geometria. Selecione para carregar.'.format(len(found)))
                load.setEnabled(bool(found))
            except (ValueError, UnicodeError, TypeError) as exc:
                status.setText('Resposta WFS inválida: {}'.format(exc))

        def query_features(fields):
            value = query.text().strip().upper()
            digits = re.sub(r'\D', '', value)
            if re.fullmatch(r'PA-\d{7}-[A-F0-9]{32}', value):
                matched = [self._pa_field_match(fields, ('tx_cod_imovel', 'cod_imovel', 'codigo_imovel', 'cod_car', 'codigo_car', 'numero_car', 'car'))]
                filter_value = value
            elif len(digits) in (11, 14) and re.fullmatch(r'[\d.\-/ ]+', value):
                # A camada do Para distingue documento do proprietario e do cadastrante.
                candidates = ('tx_cpf_cnpj_proprietario', 'tx_cpf_cadastrante', 'tx_cnpj_cadastrante')
                matched = [self._pa_field_match(fields, (name,)) for name in candidates]
                filter_value = digits
            else:
                search.setEnabled(True)
                status.setText('Informe código CAR do Pará válido ou CPF/CNPJ com 11 ou 14 dígitos.')
                return
            matched = list(dict.fromkeys(field for field in matched if field))
            if not matched:
                search.setEnabled(True)
                status.setText('A camada vw_imoveis não expõe um atributo compatível com este tipo de consulta.')
                return
            # Entradas validadas; nomes dos atributos provêm do DescribeFeatureType.
            cql = ' OR '.join("{}='{}'".format(field, filter_value) for field in matched)
            if len(matched) > 1:
                cql = '(' + cql + ')'
            params = {'service':'WFS','version':'1.0.0','request':'GetFeature',
                      'typeName':self.car_pa_layer,'outputFormat':'application/json',
                      'srsName':'EPSG:4326','maxFeatures':'20','cql_filter':cql}
            status.setText('Consultando imóveis...')
            self._pa_request(params, done_features)

        def done_schema(http, error, payload):
            if http != 200:
                search.setEnabled(True)
                status.setText('Não foi possível consultar o esquema WFS (HTTP {}).'.format(http or 'sem resposta'))
                return
            try:
                root = ET.fromstring(payload)
                fields = [e.attrib['name'] for e in root.iter() if e.tag.rsplit('}',1)[-1] == 'element' and e.attrib.get('name')]
                schema['fields'] = fields
                query_features(fields)
            except Exception as exc:
                search.setEnabled(True)
                status.setText('Falha ao interpretar o esquema WFS: {}'.format(exc))

        def start():
            if not query.text().strip():
                status.setText('Informe o código CAR ou CPF/CNPJ.')
                return
            search.setEnabled(False)
            load.setEnabled(False)
            found.clear()
            results.clear()
            if schema['fields'] is not None:
                query_features(schema['fields'])
            else:
                status.setText('Identificando atributos disponíveis na camada vw_imoveis...')
                self._pa_request({'service':'WFS','version':'1.0.0','request':'DescribeFeatureType',
                                  'typeName':self.car_pa_layer}, done_schema)

        def add_selected():
            idx = results.currentRow()
            if idx < 0 or idx >= len(found):
                status.setText('Selecione um imóvel na lista.')
                return
            feature = dict(found[idx])
            # Não exportar nomes nem documentos pessoais para a camada/GeoJSON.
            private_fields = ('tx_cpf_cnpj_proprietario', 'tx_cpf_cadastrante',
                              'tx_cnpj_cadastrante', 'tx_nome_proprietario',
                              'tx_nome_cadastrante', 'tx_nome_responsavel')
            feature['properties'] = {k: v for k, v in (feature.get('properties') or {}).items()
                                     if k.lower() not in private_fields}
            obj = {'type':'FeatureCollection','features':[feature]}
            # O GeoJSON temporário não inclui nomes pessoais ou documentos conhecidos.
            import tempfile
            path = None
            try:
                fd, path = tempfile.mkstemp(suffix='.geojson', prefix='geonexus_car_pa_')
                with os.fdopen(fd, 'w', encoding='utf-8') as out:
                    json.dump(obj, out, ensure_ascii=False)
                layer = QgsVectorLayer(path, 'CAR Pará — imóvel consultado', 'ogr')
                if not layer.isValid():
                    raise ValueError('Geometria inválida ou incompatível')
                if layer.featureCount() == 0 or layer.extent().isEmpty():
                    raise ValueError('O imóvel não possui geometria espacial válida para enquadramento')
                # Contorno vermelho, preenchimento totalmente transparente.
                # A simbologia é aplicada apenas à camada CAR carregada nesta consulta.
                if layer.geometryType() == 2:  # QgsWkbTypes.PolygonGeometry
                    symbol = QgsFillSymbol.createSimple({
                        'style': 'no',
                        'outline_style': 'solid',
                        'outline_color': '255,0,0,255',
                        'outline_width': '0.8',
                        'outline_width_unit': 'MM',
                        'joinstyle': 'round',
                    })
                    if symbol is None:
                        raise ValueError('Não foi possível configurar o contorno do imóvel')
                    layer.renderer().setSymbol(symbol)
                project = QgsProject.instance()
                project.addMapLayer(layer)
                canvas = self.iface.mapCanvas()
                # A resposta WFS usa EPSG:4326; o projeto QGIS pode estar em outro SRC.
                # Transformar a extensão evita zoom em local incorreto.
                extent = QgsCoordinateTransform(
                    layer.crs(), canvas.mapSettings().destinationCrs(), project
                ).transformBoundingBox(layer.extent())
                if extent.isEmpty():
                    raise ValueError('Não foi possível calcular a extensão do imóvel')
                # Margem de 8% ao redor do imóvel para enquadramento confortável.
                extent.scale(1.08)
                def zoom_to_property():
                    canvas.setExtent(extent)
                    canvas.refresh()
                status.setText('Imóvel carregado; centralizando o mapa...')
                dlg.accept()
                QTimer.singleShot(0, zoom_to_property)
                # Arquivo temporário é mantido enquanto a camada QGIS o utiliza.
            except Exception as exc:
                status.setText('Falha ao carregar geometria: {}'.format(exc))

        search.clicked.connect(start)
        query.returnPressed.connect(start)
        load.clicked.connect(add_selected)
        dlg.exec()

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
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
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
            if error != QNetworkReply.NetworkError.NoError:
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
                QgsMessageLog.logMessage('Projeto com {} camadas WMS autenticadas.'.format(count), 'GeoNexus', Qgis.MessageLevel.Info)
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
