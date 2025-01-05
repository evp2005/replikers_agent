from flask import Flask, request, jsonify
from agente import multi_agent_app

app_flask = Flask(__name__)

@app_flask.route('/api/ask', methods=['POST'])
def ask():
    data = request.json
    input_text = data.get('pregunta')

    if not input_text:
        return jsonify({'error': 'Se requiere una pregunta'}), 400

    # Hacer la consulta al agente IA
    try:
        respuesta = multi_agent_app.query(input_text=input_text)
        return jsonify({'respuesta': respuesta})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app_flask.run(Debug=True)  # Activar el modo debug