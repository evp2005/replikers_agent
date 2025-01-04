from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/api/ejemplo', methods=['GET', 'POST'])
def ejemplo_endpoint():
    if request.method == 'GET':
        return jsonify({"mensaje": "¡Hola, este es un ejemplo de API con Flask!"})
    
    if request.method == 'POST':
        data = request.get_json()  # Leer datos en formato JSON del cuerpo de la solicitud
        if not data or 'nombre' not in data:
            return jsonify({"error": "El campo 'nombre' es obligatorio."}), 400
        
        nombre = data['nombre']
        return jsonify({"mensaje": f"Hola, {nombre}. Tu solicitud POST fue recibida."}), 201

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)