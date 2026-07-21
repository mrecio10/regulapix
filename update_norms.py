#!/usr/bin/env python3
"""
update_norms.py
Executado diariamente pelo GitHub Actions.
Consulta a API do Claude com busca web, detecta novas normas do BCB
sobre o Pix e as injeta no regulapix.html.
"""
import anthropic
import json
import re
import os
import sys
from datetime import date

HTML_FILE = 'regulapix.html'
MAX_LEAF_CHARS = 41  # mesmo limite do JS fitLeafText()


def fit_leaf_text(text):
    text = text.strip()
    if len(text) <= MAX_LEAF_CHARS:
        return text
    return text[:MAX_LEAF_CHARS - 1].rstrip() + '…'


def main():
    # ── 1. Ler o HTML atual ───────────────────────────────────
    with open(HTML_FILE, 'r', encoding='utf-8') as f:
        html = f.read()

    # ── 2. Extrair CURRENT_NORMS ──────────────────────────────
    match = re.search(r'const CURRENT_NORMS = \[(.*?)\];', html, re.DOTALL)
    if not match:
        print('ERRO: CURRENT_NORMS não encontrado no HTML')
        sys.exit(1)

    current_norms = re.findall(r"'([^']+)'", match.group(1))
    print(f'Normas já mapeadas: {len(current_norms)}')

    # ── 3. Extrair AUTO_NORMS já existentes ───────────────────
    auto_match = re.search(
        r'// __AUTO_NORMS_START__\n(.*?)// __AUTO_NORMS_END__',
        html, re.DOTALL
    )
    existing_auto = []
    if auto_match:
        raw = auto_match.group(1).strip()
        if raw:
            try:
                cleaned = raw.rstrip(',').strip()
                existing_auto = json.loads('[' + cleaned + ']')
            except Exception:
                existing_auto = []
    print(f'Normas AUTO já injetadas: {len(existing_auto)}')

    # ── 4. Chamar a API do Claude com busca web ───────────────
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print('ERRO: variável ANTHROPIC_API_KEY não definida')
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    all_known = current_norms + [n.get('leafText', '') for n in existing_auto]
    prompt = (
        f'Pesquise na web se existem NOVAS resoluções, circulares ou instruções '
        f'normativas do Banco Central do Brasil (BCB) sobre o Pix publicadas '
        f'após abril de 2026.\n\n'
        f'Normas já incluídas (não repetir): {", ".join(current_norms)}.\n\n'
        f'Retorne APENAS JSON válido, sem markdown:\n'
        f'{{\n'
        f'  "newNorms": [\n'
        f'    {{\n'
        f'      "tipo": "Resolução BCB",\n'
        f'      "numero": 560,\n'
        f'      "leafText": "Res. BCB nº 560/2026 — resumo (máx 41 chars)",\n'
        f'      "branch": "seguranca"\n'
        f'    }}\n'
        f'  ],\n'
        f'  "summary": "mensagem"\n'
        f'}}\n\n'
        f'Branches: legal, spi, participantes, chaves, modalidades, limites, seguranca, tarifas.\n'
        f'leafText DEVE ter no máximo 41 caracteres.\n'
        f'Se não houver normas novas: {{"newNorms": [], "summary": "Nenhuma norma nova."}}'
    )

    print('Consultando API do Claude...')
    response = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=1000,
        system=(
            'Você é especialista em regulamentação brasileira do Pix. '
            'Sua resposta final deve conter SOMENTE o objeto JSON, começando '
            'diretamente com "{" e terminando com "}". '
            'NUNCA escreva nenhuma frase, explicação ou observação antes ou '
            'depois do JSON — nem mesmo uma linha dizendo qual norma você encontrou. '
            'Não use markdown nem blocos de código.'
        ),
        tools=[{'type': 'web_search_20250305', 'name': 'web_search'}],
        messages=[{'role': 'user', 'content': prompt}],
    )

    text = ''.join(getattr(b, 'text', '') for b in response.content)
    text = re.sub(r'```json|```', '', text).strip()

    # A API às vezes adiciona uma frase explicativa antes do JSON,
    # mesmo quando instruída a responder só com JSON. Extraímos apenas
    # o trecho entre a primeira "{" e a última "}" para ser tolerante a isso.
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if json_match:
        text = json_match.group(0)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f'ERRO ao parsear JSON: {e}\nResposta recebida:\n{text}')
        sys.exit(1)

    new_norms = data.get('newNorms', [])
    print(f'Resultado: {data.get("summary", "")}')

    if not new_norms:
        # Mesmo sem normas novas, atualiza a data para mostrar que o robô rodou hoje
        today_str = date.today().strftime('%d/%m/%Y')
        html = re.sub(
            r"const LAST_AUTO_UPDATE = '[^']*';.*",
            f"const LAST_AUTO_UPDATE = '{today_str}'; // __LAST_UPDATE__",
            html,
        )
        with open(HTML_FILE, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f'Data atualizada para {today_str} — nenhuma norma nova encontrada.')
        sys.exit(0)

    # ── 5. Mesclar novas normas com as existentes ─────────────
    # Três camadas de proteção contra duplicatas:
    # 1. Número da norma já em CURRENT_NORMS (verificação no HTML)
    # 2. Número da norma já em AUTO_NORMS (verificação no array existente)
    # 3. Texto similar por normalização (truncagem, maiúsculas)
    def norm_text(s):
        return s.replace('…', '').strip().lower()

    # Conjunto de números já conhecidos (AUTO_NORMS + HTML inteiro)
    existing_numbers = {str(n.get('numero', '')) for n in existing_auto}

    existing_norms_set = [(n['leafText'], norm_text(n['leafText'])) for n in existing_auto]

    def is_duplicate(new_norm):
        numero = str(new_norm.get('numero', ''))
        text   = new_norm.get('leafText', '')
        nt     = norm_text(text)

        # 1. Verificação por número — mais confiável
        if numero and numero in existing_numbers:
            return True

        # 2. Verificação por número no HTML completo (inclui BRANCHES e CURRENT_NORMS)
        if numero and (
            f'BCB nº {numero}' in html or
            f'BCB {numero}/' in html or
            f'numero": {numero}' in html.replace(' ', '') or
            f"/{numero}'" in html
        ):
            return True

        # 3. Verificação por texto similar (fallback)
        for _, en in existing_norms_set:
            if nt == en or nt.startswith(en) or en.startswith(nt):
                return True

        return False

    added = []
    for norm in new_norms:
        norm['leafText'] = fit_leaf_text(norm['leafText'])
        if not is_duplicate(norm):
            existing_auto.append(norm)
            existing_numbers.add(str(norm.get('numero', '')))
            existing_norms_set.append((norm['leafText'], norm_text(norm['leafText'])))
            added.append(norm)

            # Adicionar referência em CURRENT_NORMS para evitar repetição futura
            tipo_abrev = (
                norm['tipo']
                .replace('Resolução BCB', 'Res BCB')
                .replace('Instrução Normativa BCB', 'IN BCB')
                .replace('Circular BCB', 'Circular BCB')
            )
            norm_ref = f"'{tipo_abrev} {norm['numero']}/{date.today().year}'"
            if norm_ref not in html:
                html = html.replace(
                    'const CURRENT_NORMS = [',
                    f"const CURRENT_NORMS = [\n  {norm_ref},"
                )

    if not added:
        print('Normas já estavam presentes — HTML não alterado.')
        sys.exit(0)

    # ── 6. Gerar novo bloco AUTO_NORMS ────────────────────────
    lines = [f'  {json.dumps(n, ensure_ascii=False)},' for n in existing_auto]
    new_block = '\n'.join(lines) + '\n'

    html = re.sub(
        r'// __AUTO_NORMS_START__\n.*?// __AUTO_NORMS_END__',
        f'// __AUTO_NORMS_START__\n{new_block}// __AUTO_NORMS_END__',
        html,
        flags=re.DOTALL,
    )

    # ── 7. Atualizar data da última atualização automática ────
    today_str = date.today().strftime('%d/%m/%Y')
    html = re.sub(
        r"const LAST_AUTO_UPDATE = '[^']*';.*",
        f"const LAST_AUTO_UPDATE = '{today_str}'; // __LAST_UPDATE__",
        html,
    )

    # ── 8. Salvar HTML atualizado ──────────────────────────────
    with open(HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'\n✅ {len(added)} norma(s) adicionada(s) ao {HTML_FILE}:')
    for n in added:
        print(f'  + [{n["branch"]}] {n["leafText"]}')


if __name__ == '__main__':
    main()
