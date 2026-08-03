import mysql.connector

try:
    conn = mysql.connector.connect(
        host="localhost", # Eğer Docker'da ise '127.0.0.1' dene
        user="gaun_app",
        password="BURAYA_SIFRENİ_YAZ", # .env dosyasındaki şifreni buraya gir
        database="gaun_assistant"
    )
    print("Veritabanı bağlantısı başarılı!")
    conn.close()
except Exception as e:
    print(f"Bağlantı hatası: {e}")
