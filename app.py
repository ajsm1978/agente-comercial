import os,sqlite3
from flask import Flask,request,redirect,url_for,session,render_template,jsonify
from openai import OpenAI

app=Flask(__name__)
app.secret_key=os.getenv('SECRET_KEY','dev')

configured_db=os.getenv('DATABASE_PATH','data.db')
if os.path.dirname(configured_db):
    try:
        os.makedirs(os.path.dirname(configured_db),exist_ok=True)
        DB=configured_db
    except OSError:
        DB='data.db'
else:
    DB=configured_db

def con():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

def init():
    c=con()
    c.execute('CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,agent_name TEXT,presentation TEXT,description TEXT,instagram TEXT,whatsapp TEXT,phone TEXT,email TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS knowledge(id INTEGER PRIMARY KEY,company_id INTEGER,filename TEXT,content TEXT)')
    c.commit();c.close()

def auth(): return session.get('admin')

@app.route('/admin/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        if request.form.get('password')==os.getenv('ADMIN_PASSWORD','admin123'):
            session['admin']=1
            return redirect('/admin')
        return render_template('login.html',error='Senha inválida')
    return render_template('login.html')

@app.get('/admin/logout')
def logout():
    session.clear();return redirect('/admin/login')

@app.get('/')
def home():
    return redirect('/admin/login')

@app.get('/admin')
def admin():
    if not auth(): return redirect('/admin/login')
    c=con();rows=c.execute('select * from companies order by id desc').fetchall();c.close()
    return render_template('dashboard.html',companies=rows)

@app.post('/admin/company')
def company():
    if not auth(): return redirect('/admin/login')
    f=request.form
    s=f.get('slug','').strip().lower().replace(' ','-')
    c=con()
    c.execute('insert into companies(slug,name,agent_name,presentation,description,instagram,whatsapp,phone,email) values(?,?,?,?,?,?,?,?,?)',(s,f.get('name'),f.get('agent_name') or 'Assistente',f.get('presentation',''),f.get('description',''),f.get('instagram',''),f.get('whatsapp',''),f.get('phone',''),f.get('email','')))
    c.commit();c.close();return redirect('/admin')

@app.post('/admin/company/<int:i>/knowledge')
def knowledge(i):
    if not auth(): return redirect('/admin/login')
    x=request.files.get('file')
    if x:
        c=con();c.execute('insert into knowledge(company_id,filename,content) values(?,?,?)',(i,x.filename,x.read().decode('utf8','replace')));c.commit();c.close()
    return redirect('/admin')

@app.get('/c/<slug>')
def public(slug):
    c=con();co=c.execute('select * from companies where slug=?',(slug,)).fetchone();c.close()
    if not co:return 'Empresa não encontrada',404
    return render_template('chat.html',company=co)

@app.post('/api/chat/<slug>')
def chat(slug):
    c=con();co=c.execute('select * from companies where slug=?',(slug,)).fetchone();docs=c.execute('select filename,content from knowledge where company_id=?',(co['id'],)).fetchall() if co else [];c.close()
    if not co:return jsonify(error='Empresa não encontrada'),404
    m=(request.json or {}).get('message','').strip();key=os.getenv('OPENAI_API_KEY')
    if not key:return jsonify(reply='O atendimento por IA ainda não foi configurado. Fale com a equipe da empresa.')
    ctx='\n'.join([f"Empresa: {co['name']}",f"Agente: {co['agent_name']}",f"Apresentação: {co['presentation']}",f"Descrição: {co['description']}",f"Instagram: {co['instagram']}",f"WhatsApp: {co['whatsapp']}",f"Telefone: {co['phone']}",f"E-mail: {co['email']}"]+[f"Arquivo {d['filename']}: {d['content']}" for d in docs])
    sys=f'Você é {co["agent_name"]}, assistente comercial de {co["name"]}. Responda em português, naturalmente e somente com base no contexto. Nunca invente preços, promoções ou disponibilidade. Para orçamento, encaminhe para a equipe. Para promoções, indique o Instagram. Se for assunto fora da empresa, diga que não possui essa informação e ofereça contato humano.\n{ctx}'
    try:
        r=OpenAI(api_key=key).chat.completions.create(model=os.getenv('OPENAI_MODEL','gpt-5-mini'),messages=[{'role':'system','content':sys},{'role':'user','content':m}])
        ans=r.choices[0].message.content
    except Exception:
        ans='Não consegui concluir o atendimento agora. Fale diretamente com a equipe da empresa.'
    return jsonify(reply=ans)

init()
if __name__=='__main__':app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')))
