# Revisão e ajustes dos verificadores

A infraestrutura e o pipeline passaram no verificador oficial: 70/70. Na revisão complementar, foram corrigidos três detalhes do verificador, sem alterar recursos AWS:

- O comando Lake Formation `list-resources` não aceita `--catalog-id`.
- A ordem dos administradores e das permissões devolvida pela API pode variar. A comparação agora usa JSON determinístico, mantendo todos os membros e valores; a linha de base foi migrada usando exclusivamente o snapshot salvo antes do apply e seu hash original validado.
- O `SELECT` para todas as tabelas é retornado como `TableWithColumns`, `Name: ALL_TABLES`, `ColumnWildcard: {}`. Essa forma foi conferida na resposta real. Ela é normalizada somente para `SELECT`; catálogo, grant options e escopo continuam sendo verificados.

O Claude Opus 5.5, em esforço alto, corrigiu o código e escreveu regressões. O operador conferiu a resposta real e executou os testes: 222 casos do verificador aprovados. Os testes da canonicalização também comprovam que inclusão/remoção de administrador ou mudança de permissão altera o hash.

O código dos jobs foi preservado em relação ao professor, removendo comentários explicativos. Os metadados `comment` do Glue permanecem porque são necessários para o contrato da tabela.
