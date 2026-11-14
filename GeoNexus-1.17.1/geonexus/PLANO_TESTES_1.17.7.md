# GeoNexus 1.17.7 — Plano de homologação

**Não publicar no repositório QGIS antes de concluir os testes.**

## Matriz de assinantes

- [ ] Usuário A autorizado: login, abertura de projeto e WMS sem segundo login
- [ ] Usuário B autorizado: repetir em perfil QGIS limpo
- [ ] Usuário sem acesso: login negado
- [ ] Usuário com assinatura vencida: verificar bloqueio efetivo no servidor
- [ ] Usuário sem permissão à camada: acesso negado sem vazamento de dados
- [ ] Duas instalações simultâneas: verificar política comercial, se aplicável
- [ ] QGIS 3.40 Windows com gerenciador de autenticação novo
- [ ] QGIS 3.40 Windows com gerenciador de autenticação já configurado
- [ ] Atualização sobre 1.16 com perfil QGIS existente
- [ ] Verificar os sete estados, URLs WMS, nomes de camadas e extensões
- [ ] Testar indisponibilidade de rede, timeout, cancelamento e sessão revogada
- [ ] Validar que o endpoint /geoserver/rest/workspaces.json exige autenticação; se público, não serve para validar login

## Riscos abertos

A versão atual substitui o antigo backend de assinaturas por login HTTP Basic do GeoServer. Sem bloqueio server-side de usuários vencidos, a assinatura comercial não é imposta pelo plugin. HTTP transmite credenciais sem criptografia. Projetos remotos e camadas de cada estado exigem validação operacional.
