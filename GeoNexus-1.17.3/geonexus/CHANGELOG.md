## 1.17.3 — Atualização de versão

- Versão atualizada para 1.17.3, preservando correções Qt6 e funcionalidades da versão anterior.

## 1.17.2 — compatibilidade Qt6
- Atualiza chamadas de diálogo e enums Qt para a sintaxe compatível com Qt6 e Qt5.
- Preserva consulta CAR Pará, zoom e contorno vermelho transparente.

## 1.17.1 — atualização de versão
- Atualiza os metadados e os títulos do plugin para 1.17.1.
- Preserva as correções de segurança e a compatibilidade mínima com QGIS 3.40.
- Mantém experimental=False.

## 1.17 — candidato final de verificação
- Padroniza a versão exibida e os metadados para 1.17.
- Mantém o login único WMS HTTP Basic, o catálogo de estados e as mensagens simplificadas da versão 1.17.13.
- A classificação experimental foi desativada; os testes funcionais e de assinantes ainda são recomendados.
- Atenção: GetCapabilities pode ser público e não comprova credenciais nem assinatura comercial ativa.

## 1.17.13
- Remove a mensagem de progresso de verificacao WMS da janela de login.
- Mantem as mensagens de erro e os fluxos de autenticacao existentes.

## 1.17.6
- Preserva os parametros originais das fontes WMS; insere authcfg antes de url.
- Mantem login unico e configuracao HTTP Basic do QGIS.
- Mantem URLs e projetos originais; nao altera os arquivos QGZ de origem.
- Correcao ainda requer teste funcional no QGIS 3.40.

## 1.17.5
- Inicializacao/desbloqueio assistido do armazenamento protegido do QGIS durante o primeiro login.
- Corrigida API de gravacao de credenciais: storeAuthenticationConfig.
- Reutilizacao da configuracao HTTP Basic GeoNexus por usuario.
- Login somente e concluido depois que a autenticacao local esta pronta.
- Sem armazenamento de senhas em QSettings ou projetos QGZ.

## 1.17.3
- Simplificação do login: removido aviso visual e checkbox de confirmação HTTP.
- A conexão HTTP permanece configurada como na versão anterior.
- Mantidos autenticação GeoServer REST HTTP Basic e login único nas camadas WMS.
- Nenhuma alteração no servidor ou nas credenciais armazenadas.

## 1.17.2
- Associa automaticamente a configuração HTTP Basic do QGIS às camadas WMS GeoServer dos projetos baixados.
- Mantém login único na interface GeoNexus, sem embutir senha em arquivos QGZ.
- Requer gerenciador de autenticação QGIS operacional.
- Preserva os endereços HTTP existentes (portas 8082 e 8086).
- Não altera o sistema de assinaturas, que nesta ramificação é substituído pela validação GeoServer REST.

# GeoNexus 1.17.1 (teste)

- Login GeoServer REST HTTP Basic em `http://geoservico.com.br:8086/geoserver/rest/workspaces.json`.
- Revalidação periódica de credenciais via GET; logout local (sem endpoint de logout GeoServer).
- Interface modernizada e catálogo da 1.17 preservados.
- Não há validação de assinatura comercial: GeoServer REST não fornece esse contrato por padrão.
- ATENÇÃO: HTTP transmite credenciais sem criptografia. Use somente em rede confiável.
- Exige configurar REST /workspaces.json para negar acesso anônimo; caso contrário, resposta 200 não comprova autenticação.
- Não testado com servidor real nem no QGIS; confirmar também a política de acesso às camadas.

## 1.17.4
- Insere authcfg nas fontes WMS do projeto antes de QgsProject.read().
- Restringe a alteracao ao GeoServer geoservico.com.br nas portas 8082 e 8086.
- Nao inclui senhas em URLs ou arquivos QGZ.
- Exige gerenciador de autenticacao QGIS operacional; sem fallback inseguro.


## 1.17.7 — candidato para testes controlados
- Atualizado número da versão na interface e nos metadados.
- Marcado como experimental até testes com assinantes distintos.
- Preservado integralmente o mecanismo de autenticação e WMS validado na versão 1.17.6.
- Atenção: a REST do GeoServer não valida por si só vencimento comercial, limite de dispositivos ou assinatura; não publicar como versão estável antes de validar essas regras no servidor.
