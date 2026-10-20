> **Atualização (2026-10-20):** a chamada mínima validada aqui hoje exige também o header `x-client-id` (sem ele a API responde 400) e respeita bloqueios de taxa. Ver [ADR 0001](adr/0001-convivencia-com-o-portal-do-df.md).

## Validação da chamada mínima
### Teste 1
```powershell
PS C:\Users\myPC\Desktop\dev\personal\df-remuneration-collector\docs> curl "https://www.transparencia.df.gov.br/api/remuneracao?anoExercicio=2025&mesReferencia=06&page=0&size=10&nomeServidor="


StatusCode        : 200
StatusDescription : OK
Content           : {"content":[{"codigoOrgaoArtificial":"0100802","codigoIdentificacao":"37412615","codigoMatricula":"70488525","cpf
                    Servidor":"***927115**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ADILA DE...
RawContent        : HTTP/1.1 200 OK
                    Access-Control-Allow-Origin: *
                    Access-Control-Allow-Methods: POST, GET, PUT, OPTIONS, DELETE
                    Access-Control-Max-Age: 3600
                    Access-Control-Allow-Headers: Origin, X-Requested-With, Co...
Forms             : {}
Headers           : {[Access-Control-Allow-Origin, *], [Access-Control-Allow-Methods, POST, GET, PUT, OPTIONS, DELETE], 
                    [Access-Control-Max-Age, 3600], [Access-Control-Allow-Headers, Origin, X-Requested-With, Content-Type, Accept,   
                    responseType, X-BROWSER-VERSION]...}
Images            : {}
InputFields       : {}
Links             : {}
ParsedHtml        : mshtml.HTMLDocumentClass
RawContentLength  : 10863



PS C:\Users\myPC\Desktop\dev\personal\df-remuneration-collector\docs>
```

### Teste 2
```powershell
PS C:\Users\myPC\Desktop\dev\personal\df-remuneration-collector\docs> curl "https://www.transparencia.df.gov.br/api/remuneracao?anoExercicio=2025&mesReferencia=06&page=0&size=10&nomeServidor="


StatusCode        : 200
StatusDescription : OK
Content           : {"content":[{"codigoOrgaoArtificial":"0100802","codigoIdentificacao":"37412615","codigoMatricula":"70488525","cpf
                    Servidor":"***927115**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ADILA DE...
PS C:\Users\myPC\Desktop\dev\personal\df-remuneration-collector\docs> curl "https://www.transparencia.df.gov.br/api/remuneracao?anoExercicio=2025&mesReferencia=06&page=0&size=10&nomeServidor=&tipo=csv"


StatusCode        : 200
StatusDescription : OK
Content           : {"content":[{"codigoOrgaoArtificial":"0100802","codigoIdentificacao":"37412615","codigoMatricula":"70488525","cpf
                    Servidor":"***927115**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ADILA DE...
RawContent        : HTTP/1.1 200 OK
                    Access-Control-Allow-Origin: *
                    Access-Control-Allow-Methods: POST, GET, PUT, OPTIONS, DELETE
                    Access-Control-Max-Age: 3600
                    Access-Control-Allow-Headers: Origin, X-Requested-With, Co...
Forms             : {}
Headers           : {[Access-Control-Allow-Origin, *], [Access-Control-Allow-Methods, POST, GET, PUT, OPTIONS, DELETE],
                    [Access-Control-Max-Age, 3600], [Access-Control-Allow-Headers, Origin, X-Requested-With, Content-Type, Accept, 
                    responseType, X-BROWSER-VERSION]...}
Images            : {}
InputFields       : {}
Links             : {}
ParsedHtml        : mshtml.HTMLDocumentClass
RawContentLength  : 10863



PS C:\Users\myPC\Desktop\dev\personal\df-remuneration-collector\docs>
```

### Teste 3
```powershell
PS C:\Users\myPC\Desktop\dev\personal\df-remuneration-collector\docs> curl "https://www.transparencia.df.gov.br/api/remuneracao?anoExercicio=2025&mesReferencia=06&page=0&size=10&nomeServidor="


StatusCode        : 200
StatusDescription : OK
Content           : {"content":[{"codigoOrgaoArtificial":"0100802","codigoIdentificacao":"37412615","codigoMatricula":"70488525","cpf
                    Servidor":"***927115**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ADILA DE...
PS C:\Users\myPC\Desktop\dev\personal\df-remuneration-collector\docs> curl "https://www.transparencia.df.gov.br/api/remuneracao?anoExercicio=2025&mesReferencia=06&page=0&size=10&nomeServidor=&tipo=csv"


StatusCode        : 200
StatusDescription : OK
Content           : {"content":[{"codigoOrgaoArtificial":"0100802","codigoIdentificacao":"37412615","codigoMatricula":"70488525","cpf
                    Servidor":"***927115**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ADILA DE...
RawContent        : HTTP/1.1 200 OK
PS C:\Users\myPC\Desktop\dev\personal\df-remuneration-collector\docs> curl "https://www.transparencia.df.gov.br/api/remuneracao?anoExercicio=2025&mesReferencia=06&page=0&size=10&nomeServidor=&busy=true"


StatusCode        : 200
StatusDescription : OK 
Content           : {"content":[{"codigoOrgaoArtificial":"0100802","codigoIdentificacao":"37412615","codigoMatricula":"70488525","cpf
                    Servidor":"***927115**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ADILA DE...
RawContent        : HTTP/1.1 200 OK
                    Access-Control-Allow-Origin: *
                    Access-Control-Allow-Methods: POST, GET, PUT, OPTIONS, DELETE
                    Access-Control-Max-Age: 3600
                    Access-Control-Allow-Headers: Origin, X-Requested-With, Co...
Forms             : {}
Headers           : {[Access-Control-Allow-Origin, *], [Access-Control-Allow-Methods, POST, GET, PUT, OPTIONS, DELETE],
                    [Access-Control-Max-Age, 3600], [Access-Control-Allow-Headers, Origin, X-Requested-With, Content-Type, Accept,   
                    responseType, X-BROWSER-VERSION]...}
Images            : {}
InputFields       : {}
Links             : {}
ParsedHtml        : mshtml.HTMLDocumentClass
RawContentLength  : 10863



PS C:\Users\myPC\Desktop\dev\personal\df-remuneration-collector\docs>
```

### Teste 4
```powershell
PS C:\Users\myPC\Desktop\dev\personal\df-remuneration-collector\docs> curl "https://www.transparencia.df.gov.br/api/remuneracao?anoExercicio=2025&mesReferencia=06&page=0&size=10&nomeServidor="


StatusCode        : 200
StatusDescription : OK
Content           : {"content":[{"codigoOrgaoArtificial":"0100802","codigoIdentificacao":"37412615","codigoMatricula":"70488525","cpf
                    Servidor":"***927115**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ADILA DE...
PS C:\Users\myPC\Desktop\dev\personal\df-remuneration-collector\docs> curl "https://www.transparencia.df.gov.br/api/remuneracao?anoExercicio=2025&mesReferencia=06&page=0&size=10&nomeServidor=&tipo=csv"


StatusCode        : 200
StatusDescription : OK
Content           : {"content":[{"codigoOrgaoArtificial":"0100802","codigoIdentificacao":"37412615","codigoMatricula":"70488525","cpf
                    Servidor":"***927115**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ADILA DE...
RawContent        : HTTP/1.1 200 OK
PS C:\Users\myPC\Desktop\dev\personal\df-remuneration-collector\docs> curl "https://www.transparencia.df.gov.br/api/remuneracao?anoExercicio=2025&mesReferencia=06&page=0&size=10&nomeServidor=&busy=true"


StatusCode        : 200
StatusDescription : OK 
Content           : {"content":[{"codigoOrgaoArtificial":"0100802","codigoIdentificacao":"37412615","codigoMatricula":"70488525","cpf
                    Servidor":"***927115**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ADILA DE...
PS C:\Users\myPC\Desktop\dev\personal\df-remuneration-collector\docs> curl -H "X-BROWSER-VERSION: teste-manual" "https://www.transparencia.df.gov.br/api/remuneracao?anoExercicio=2025&mesReferencia=06&page=0&size=10&nomeServidor="

C:\Users\myPC>curl -H "X-BROWSER-VERSION: teste-manual" "https://www.transparencia.df.gov.br/api/remuneracao?anoExercicio=2025&mesReferencia=06&page=0&size=10&nomeServidor="
{"content":[{"codigoOrgaoArtificial":"0100802","codigoIdentificacao":"37412615","codigoMatricula":"70488525","cpfServidor":"***927115**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ADILA DE JESUS VIEIRA                            ","codigoOrgao":"802","nomeOrgao":"SECRETARIA DE ESTADO DE EDUCACAO - TEMPORARIO                                   ","situacaoFuncional":"ATIVO","situacaoFuncionalDetalhada":"ATIVO                         ","cargo":"CONTRATO TEMPORARIO           ","funcao":"                                                            ","valorRemuneracaoBasica":6352.64,"valorBeneficios":640.00,"valorFuncoes":0.00,"valorComissaoConselheiro":0.00,"valorHoraExtra":0.00,"valorVerbasEventuais":0.00,"valorVerbasJudiciais":0.00,"valorReceitasMesesAnteriores":0.00,"valorReposicaoDescontosMaior":0.00,"valorLicencaPremio":0.00,"valorImpostoRenda":621.39,"valorSeguridadeSocial":788.55,"valorRedutorTeto":0.00,"valorDescontosMesesAnteriores":0.00,"valorReposicaoPagamentoMaior":0.00,"valorExpurgo":0.00,"valorLiquido":5582.70,"valorBruto":6992.64},{"codigoOrgaoArtificial":"0100652","codigoIdentificacao":"37393573","codigoMatricula":"02563037","cpfServidor":"***345061**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ADRIANA AIRES CIRQUEIRA                          ","codigoOrgao":"652","nomeOrgao":"SECRETARIA DE ESTADO DE EDUCACAO DO DISTRITO FEDERAL                            ","situacaoFuncional":"ATIVO","situacaoFuncionalDetalhada":"ATIVO                         ","cargo":"PROFESSOR DE EDUC. BASICA     ","funcao":"                                                            ","valorRemuneracaoBasica":4553.32,"valorBeneficios":640.00,"valorFuncoes":0.00,"valorComissaoConselheiro":0.00,"valorHoraExtra":0.00,"valorVerbasEventuais":0.00,"valorVerbasJudiciais":0.00,"valorReceitasMesesAnteriores":0.00,"valorReposicaoDescontosMaior":2276.66,"valorLicencaPremio":0.00,"valorImpostoRenda":706.56,"valorSeguridadeSocial":956.19,"valorRedutorTeto":0.00,"valorDescontosMesesAnteriores":0.00,"valorReposicaoPagamentoMaior":0.00,"valorExpurgo":-1193.58,"valorLiquido":5807.23,"valorBruto":7469.98},{"codigoOrgaoArtificial":"0100652","codigoIdentificacao":"37394759","codigoMatricula":"02580152","cpfServidor":"***894873**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ADRIELLY RAMOS DE OLIVEIRA                       ","codigoOrgao":"652","nomeOrgao":"SECRETARIA DE ESTADO DE EDUCACAO DO DISTRITO FEDERAL                            ","situacaoFuncional":"ATIVO","situacaoFuncionalDetalhada":"ATIVO                         ","cargo":"GES.POL.PUB.G.E. C CONTABEIS  ","funcao":"                                                            ","valorRemuneracaoBasica":5569.53,"valorBeneficios":677.41,"valorFuncoes":0.00,"valorComissaoConselheiro":0.00,"valorHoraExtra":0.00,"valorVerbasEventuais":0.00,"valorVerbasJudiciais":0.00,"valorReceitasMesesAnteriores":0.00,"valorReposicaoDescontosMaior":0.00,"valorLicencaPremio":0.00,"valorImpostoRenda":316.89,"valorSeguridadeSocial":779.73,"valorRedutorTeto":0.00,"valorDescontosMesesAnteriores":0.00,"valorReposicaoPagamentoMaior":0.00,"valorExpurgo":0.00,"valorLiquido":5150.32,"valorBruto":6246.94},{"codigoOrgaoArtificial":"0100652","codigoIdentificacao":"37330067","codigoMatricula":"00208760","cpfServidor":"***944501**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" AILDE LIMA E SILVA DA CRUZ                       ","codigoOrgao":"652","nomeOrgao":"SECRETARIA DE ESTADO DE EDUCACAO DO DISTRITO FEDERAL                            ","situacaoFuncional":"INATIVO","situacaoFuncionalDetalhada":"APOSENTADO                    ","cargo":"PROFESSOR DE EDUC. BASICA     ","funcao":"                                                            ","valorRemuneracaoBasica":10671.08,"valorBeneficios":0.00,"valorFuncoes":0.00,"valorComissaoConselheiro":0.00,"valorHoraExtra":0.00,"valorVerbasEventuais":6402.64,"valorVerbasJudiciais":0.00,"valorReceitasMesesAnteriores":0.00,"valorReposicaoDescontosMaior":0.00,"valorLicencaPremio":0.00,"valorImpostoRenda":1204.60,"valorSeguridadeSocial":1082.24,"valorRedutorTeto":0.00,"valorDescontosMesesAnteriores":0.00,"valorReposicaoPagamentoMaior":0.00,"valorExpurgo":-3506.67,"valorLiquido":14786.88,"valorBruto":17073.72},{"codigoOrgaoArtificial":"0100050","codigoIdentificacao":"37252846","codigoMatricula":"17235219","cpfServidor":"***221921**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ALESSANDRA RODRIGUES BATISTA                     ","codigoOrgao":"50","nomeOrgao":"CASA CIVIL DO DISTRITO FEDERAL                                                  ","situacaoFuncional":"ATIVO","situacaoFuncionalDetalhada":"ATIVO                         ","cargo":"                              ","funcao":"SEGURANCA DE PESSOAL, 3 SGT/CB/SD PM                        ","valorRemuneracaoBasica":1543.66,"valorBeneficios":0.00,"valorFuncoes":0.00,"valorComissaoConselheiro":0.00,"valorHoraExtra":0.00,"valorVerbasEventuais":0.00,"valorVerbasJudiciais":0.00,"valorReceitasMesesAnteriores":0.00,"valorReposicaoDescontosMaior":0.00,"valorLicencaPremio":0.00,"valorImpostoRenda":463.92,"valorSeguridadeSocial":0.00,"valorRedutorTeto":0.00,"valorDescontosMesesAnteriores":0.00,"valorReposicaoPagamentoMaior":0.00,"valorExpurgo":0.00,"valorLiquido":1079.74,"valorBruto":1543.66},{"codigoOrgaoArtificial":"0100652","codigoIdentificacao":"37396194","codigoMatricula":"02595311","cpfServidor":"***936591**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ALISSON ALAZAFE SILVA BATISTA DE MORAES          ","codigoOrgao":"652","nomeOrgao":"SECRETARIA DE ESTADO DE EDUCACAO DO DISTRITO FEDERAL                            ","situacaoFuncional":"ATIVO","situacaoFuncionalDetalhada":"ATIVO                         ","cargo":"PROFESSOR DE EDUC. BASICA     ","funcao":"                                                            ","valorRemuneracaoBasica":4658.68,"valorBeneficios":640.00,"valorFuncoes":0.00,"valorComissaoConselheiro":0.00,"valorHoraExtra":0.00,"valorVerbasEventuais":0.00,"valorVerbasJudiciais":0.00,"valorReceitasMesesAnteriores":0.00,"valorReposicaoDescontosMaior":1996.57,"valorLicencaPremio":0.00,"valorImpostoRenda":665.23,"valorSeguridadeSocial":931.73,"valorRedutorTeto":0.00,"valorDescontosMesesAnteriores":0.00,"valorReposicaoPagamentoMaior":0.00,"valorExpurgo":-2009.46,"valorLiquido":5698.29,"valorBruto":7295.25},{"codigoOrgaoArtificial":"0100802","codigoIdentificacao":"37412749","codigoMatricula":"70489963","cpfServidor":"***992570**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ANA CRISTINA GRACA MOREIRA                       ","codigoOrgao":"802","nomeOrgao":"SECRETARIA DE ESTADO DE EDUCACAO - TEMPORARIO                                   ","situacaoFuncional":"ATIVO","situacaoFuncionalDetalhada":"ATIVO                         ","cargo":"CONTRATO TEMPORARIO           ","funcao":"                                                            ","valorRemuneracaoBasica":6015.76,"valorBeneficios":640.00,"valorFuncoes":0.00,"valorComissaoConselheiro":0.00,"valorHoraExtra":0.00,"valorVerbasEventuais":0.00,"valorVerbasJudiciais":0.00,"valorReceitasMesesAnteriores":0.00,"valorReposicaoDescontosMaior":1203.15,"valorLicencaPremio":0.00,"valorImpostoRenda":826.26,"valorSeguridadeSocial":909.83,"valorRedutorTeto":0.00,"valorDescontosMesesAnteriores":0.00,"valorReposicaoPagamentoMaior":0.00,"valorExpurgo":0.00,"valorLiquido":6122.82,"valorBruto":7858.91},{"codigoOrgaoArtificial":"0100652","codigoIdentificacao":"37340383","codigoMatricula":"00425559","cpfServidor":"***897031**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ANA LOURENA DOS SANTOS RODRIGUES                 ","codigoOrgao":"652","nomeOrgao":"SECRETARIA DE ESTADO DE EDUCACAO DO DISTRITO FEDERAL                            ","situacaoFuncional":"INATIVO","situacaoFuncionalDetalhada":"APOSENTADO                    ","cargo":"ANA.POL.PUB.G.E.EDU EM SAUDE  ","funcao":"                                                            ","valorRemuneracaoBasica":10620.82,"valorBeneficios":0.00,"valorFuncoes":0.00,"valorComissaoConselheiro":0.00,"valorHoraExtra":0.00,"valorVerbasEventuais":350.96,"valorVerbasJudiciais":0.00,"valorReceitasMesesAnteriores":0.00,"valorReposicaoDescontosMaior":0.00,"valorLicencaPremio":2750.58,"valorImpostoRenda":1716.31,"valorSeguridadeSocial":1075.20,"valorRedutorTeto":0.00,"valorDescontosMesesAnteriores":0.00,"valorReposicaoPagamentoMaior":0.00,"valorExpurgo":0.00,"valorLiquido":10930.85,"valorBruto":13722.36},{"codigoOrgaoArtificial":"0100652","codigoIdentificacao":"37374900","codigoMatricula":"02009862","cpfServidor":"***037051**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ANA LUCIA DE MATOS GARCES-PARES                  ","codigoOrgao":"652","nomeOrgao":"SECRETARIA DE ESTADO DE EDUCACAO DO DISTRITO FEDERAL                            ","situacaoFuncional":"ATIVO","situacaoFuncionalDetalhada":"ATIVO                         ","cargo":"PEDAGOGO - ORIENT EDUC.       ","funcao":"                                                            ","valorRemuneracaoBasica":6840.87,"valorBeneficios":640.00,"valorFuncoes":0.00,"valorComissaoConselheiro":0.00,"valorHoraExtra":0.00,"valorVerbasEventuais":0.00,"valorVerbasJudiciais":0.00,"valorReceitasMesesAnteriores":0.00,"valorReposicaoDescontosMaior":3420.43,"valorLicencaPremio":0.00,"valorImpostoRenda":1518.06,"valorSeguridadeSocial":1436.58,"valorRedutorTeto":0.00,"valorDescontosMesesAnteriores":0.00,"valorReposicaoPagamentoMaior":0.00,"valorExpurgo":0.00,"valorLiquido":7946.66,"valorBruto":10901.30},{"codigoOrgaoArtificial":"0100802","codigoIdentificacao":"37409467","codigoMatricula":"70455309","cpfServidor":"***304471**","anoExercicio":2025,"mesReferencia":6,"nomeServidor":" ANA PAULA DE SOUZA VIDAL ARAUJO                  ","codigoOrgao":"802","nomeOrgao":"SECRETARIA DE ESTADO DE EDUCACAO - TEMPORARIO                                   ","situacaoFuncional":"ATIVO","situacaoFuncionalDetalhada":"ATIVO                         ","cargo":"CONTRATO TEMPORARIO           ","funcao":"                                                            ","valorRemuneracaoBasica":7218.91,"valorBeneficios":640.00,"valorFuncoes":0.00,"valorComissaoConselheiro":0.00,"valorHoraExtra":0.00,"valorVerbasEventuais":0.00,"valorVerbasJudiciais":0.00,"valorReceitasMesesAnteriores":0.00,"valorReposicaoDescontosMaior":0.00,"valorLicencaPremio":0.00,"valorImpostoRenda":826.26,"valorSeguridadeSocial":909.83,"valorRedutorTeto":0.00,"valorDescontosMesesAnteriores":0.00,"valorReposicaoPagamentoMaior":0.00,"valorExpurgo":0.00,"valorLiquido":6122.82,"valorBruto":7858.91}],"pageable":{"sort":null,"pageSize":10,"pageNumber":0,"offset":0},"totalizador":{"valorLiquido":2752543542.32,"valorBruto":3465501251.34},"totalPages":25906,"last":false,"totalElements":259053,"numberOfElements":10,"sort":null,"first":true,"size":10,"number":0}
C:\Users\myPC>
```

