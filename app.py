import os
from flask import Flask
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev')
@app.get('/')
def home():
    return 'Agente Comercial'
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT','10000')))
