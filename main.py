from flask import Flask, request, jsonify, render_template
import sqlite3

app = Flask(__name__)

@app.route('/api/v1/submit-lead', methods=['POST'])
def process_financial_lead():
    name = request.form.get('client_name')
    credit = request.form.get('credit_score')
    amount = request.form.get('loan_amount')
    
    if not name or not amount:
        return jsonify({"status": "ERROR", "message": "Missing criteria parameters."}), 400

    conn = sqlite3.connect('bizstack.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO leads (name, credit_tier, requested_capital) 
        VALUES (?, ?, ?)
    ''', (name, credit, float(amount)))
    conn.commit()
    conn.close()
    
    return jsonify({
        "status": "INITIALIZED",
        "message": f"Lead profile for {name} synchronized into database grid."
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
