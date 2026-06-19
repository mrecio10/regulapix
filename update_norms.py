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
            'Responda APENAS com JSON válido, sem markdown, sem texto adicional.'
        ),
        tools=[{'type': 'web_search_20250305', 'name': 'web_search'}],
        messages=[{'role': 'user', 'content': prompt}],
    )

    text = ''.join(getattr(b, 'text', '') for b in response.content)
    text = re.sub(r'```json|```', '', text).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f'ERRO ao parsear JSON: {e}\nResposta recebida:\n{text}')
        sys.exit(1)

    new_norms = data.get('newNorms', [])
    print(f'Resultado: {data.get("summary", "")}')

    if not new_norms:
        print('Nenhuma norma nova — HTML não alterado.')
        sys.exit(0)

    # ── 5. Mesclar novas normas com as existentes ─────────────
    existing_texts = {n['leafText'] for n in existing_auto}
    added = []
    for norm in new_norms:
        norm['leafText'] = fit_leaf_text(norm['leafText'])
        if norm['leafText'] not in existing_texts:
            existing_auto.append(norm)
            existing_texts.add(norm['leafText'])
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

    # ── 7. Salvar HTML atualizado ─────────────────────────────
    with open(HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'\n✅ {len(added)} norma(s) adicionada(s) ao {HTML_FILE}:')
    for n in added:
        print(f'  + [{n["branch"]}] {n["leafText"]}')


if __name__ == '__main__':
    main()
