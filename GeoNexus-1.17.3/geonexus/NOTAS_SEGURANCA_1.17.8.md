# GeoNexus 1.17.8 — Candidato de testes

- Removida a dependência de `/geoserver/rest/workspaces.json` no login e na verificação periódica.
- A verificação utiliza GetCapabilities WMS, preservando a configuração HTTP Basic e a correção de fontes WMS da versão 1.17.6.
- **Limitação importante:** GetCapabilities pode ser público. HTTP 200 não prova que usuário/senha foram validados, nem que uma assinatura está ativa. A autorização efetiva das camadas continua a cargo do GeoServer. Não publicar como controle de assinaturas sem endpoint de autenticação protegido, específico para usuários comuns.
- O protocolo HTTP transmite credenciais sem criptografia.
- Testar usuário comum, administrador, credenciais inválidas, usuário bloqueado, cada estado e QGIS novo.
