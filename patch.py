import os

# 1. Patch main.py
if os.path.exists('main.py'):
    with open('main.py', 'r') as f:
        code = f.read()
    
    # Target how the dictionary array builds your ledger metrics
    if 'gross_volume' in code and 'platform_cut' not in code:
        # We look for where it sets the Gross Volume formatting
        old_context = '"gross_volume": f"${gross_volume:,.2f}",'
        if old_context not in code:
            # Fallback if there are spaces or single quotes
            old_context = "'gross_volume': f'${gross_volume:,.2f}',"
            
        if old_context in code:
            new_context = old_context + '\n    "platform_cut": f"${gross_volume * 0.03:,.2f}",\n    "net_user_claims": f"${gross_volume * 0.97:,.2f}",'
            code = code.replace(old_context, new_context)
            with open('main.py', 'w') as f:
                f.write(code)
            print('✅ Backend file successfully patched!')
        else:
            print('⚠️ Could not find exact gross_volume formatting string inside main.py.')
    else:
        print('ℹ️ main.py already contains patch parameters or gross_volume variable is missing.')

# 2. Patch templates/dashboard.html
if os.path.exists('templates/dashboard.html'):
    with open('templates/dashboard.html', 'r') as f:
        html = f.read()

    target_phrase = "{{ gross_volume | default('$0.00') }}"
    if 'platform_cut' not in html:
        # Locate the structural list or grid layout item
        old_block = "Gross Volume:"
        for line in html.split('\n'):
            if 'gross_volume' in line and ('text-' in line or 'font-' in line):
                old_block = line
                break
                
        if old_block in html:
            # Reconstruct the grid matrix elements cleanly matching your style setup
            new_block = old_block + """
        </div>
        <div>
            <span class="block text-xs uppercase tracking-wider font-semibold text-slate-500">⚡ Your 3% Cut:</span>
            <span class="text-lg font-mono text-amber-400 font-bold">{{ platform_cut | default('$0.00') }}</span>
        </div>
        <div>
            <span class="block text-xs uppercase tracking-wider font-semibold text-slate-500">Net User Claims:</span>
            <span class="text-lg font-mono text-cyan-400 font-bold">{{ net_user_claims | default('$0.00') }}</span>"""
            html = html.replace(old_block, new_block)
            with open('templates/dashboard.html', 'w') as f:
                f.write(html)
            print('✅ Frontend layout successfully patched!')
        else:
            print('⚠️ Frontend layout structure marker not found.')
    else:
        print('ℹ️ Frontend layout already modified.')
