"""SQLite copy-only constraint conversion preserving original DDL and triggers."""
import re

_TOKEN = re.compile(r'''\s+|--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|"(?:""|[^"])*"|`(?:``|[^`])*`|\[[^\]]*\]|\w+|.''', re.S)


def _tokens(sql):
    return [(m.start(), m.end(), m.group()) for m in _TOKEN.finditer(sql)
            if not m.group().isspace() and not m.group().startswith(('--','/*'))]


def _identifier(token):
    if token.startswith(('"','`','[')):
        return token[1:-1].replace(token[0] * 2, token[0]).lower()
    return token.lower()


def _without_unique(definition, column):
    tokens = _tokens(definition)
    if not tokens:
        raise ValueError('Empty SQLite column declaration')
    unique = [i for i, token in enumerate(tokens) if token[2].upper() == 'UNIQUE']
    if not unique:
        return definition
    if _identifier(tokens[0][2]) == column:
        # Remove only an unquoted inline keyword, never quoted defaults/text.
        for i in reversed(unique):
            start, end = tokens[i][:2]
            if i >= 2 and tokens[i-2][2].upper() == 'CONSTRAINT':
                start = tokens[i-2][0]
            if [t[2].upper() for t in tokens[i+1:i+3]] == ['ON','CONFLICT']:
                if len(tokens) <= i+3:
                    raise ValueError('Invalid SQLite conflict clause')
                end = tokens[i+3][1]
            definition = definition[:start] + definition[end:]
        return definition
    i = unique[0]
    if i not in (0, 2) or (i == 2 and tokens[0][2].upper() != 'CONSTRAINT'):
        return definition
    terms = tokens[i+1:]
    if len(terms) >= 3 and terms[0][2] == '(' and terms[2][2] == ')' and _identifier(terms[1][2]) == column:
        return None
    return definition


def scope_unique(connection, table, column, constraint):
    """Run inside the caller's offline transaction with foreign_keys=OFF."""
    quote = lambda value: '"' + value.replace('"','""') + '"'
    indexes = connection.execute(f'PRAGMA index_list({quote(table)})').fetchall()
    targets = []
    for row in indexes:
        columns = [item[2] for item in connection.execute(f'PRAGMA index_info({quote(row[1])})')]
        if row[2] and columns == [column]:
            targets.append(row)
    if any(row[3] == 'u' for row in targets):
        sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0]
        tokens = _tokens(sql)
        opening = next(i for i,t in enumerate(tokens) if t[2] == '(')
        depth, start, definitions, closing = 0, tokens[opening][1], [], None
        for _, (left, right, token) in enumerate(tokens[opening+1:]):
            if token == '(':
                depth += 1
            elif token == ')':
                if depth == 0:
                    definitions.append(sql[start:left]); closing = left; break
                depth -= 1
            elif token == ',' and depth == 0:
                definitions.append(sql[start:left]); start = right
        if closing is None:
            raise ValueError('Unsupported SQLite table DDL')
        converted = [_without_unique(term,column) for term in definitions]
        if converted == definitions:
            raise ValueError('Cannot safely convert inline UNIQUE for ' + table)
        temporary = '_character_rebuild_' + table
        columns = [row[1] for row in connection.execute(f'PRAGMA table_xinfo({quote(table)})') if row[6] == 0]
        names = ','.join(map(quote, columns))
        resources = connection.execute("SELECT name,sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",(table,)).fetchall()
        excluded = {row[1] for row in targets}
        before = connection.execute(f'SELECT {names} FROM {quote(table)}').fetchall()
        connection.execute('CREATE TABLE ' + quote(temporary) + ' (' + ','.join(term for term in converted if term is not None) + sql[closing:])
        connection.execute(f'INSERT INTO {quote(temporary)} ({names}) SELECT {names} FROM {quote(table)}')
        after = connection.execute(f'SELECT {names} FROM {quote(temporary)}').fetchall()
        if before != after:
            raise ValueError('SQLite constraint rebuild changed row contents')
        connection.execute('DROP TABLE ' + quote(table))
        connection.execute(f'ALTER TABLE {quote(temporary)} RENAME TO {quote(table)}')
        for name, ddl in resources:
            if name not in excluded:
                connection.execute(ddl)
    else:
        for row in targets:
            connection.execute('DROP INDEX ' + quote(row[1]))
    connection.execute(f'CREATE INDEX IF NOT EXISTS {quote("ix_"+table+"_"+column)} ON {quote(table)}({quote(column)})')
    connection.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS {quote(constraint)} ON {quote(table)}(character_id,{quote(column)})')
