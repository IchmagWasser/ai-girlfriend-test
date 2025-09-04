#!/usr/bin/env python3
"""
Verbessertes Database Reset Script
Löscht die Datenbank und erstellt sie komplett neu mit Standard-Benutzern
"""

import sqlite3
import os
import hashlib

DB_PATH = "users.db"

def hash_password(password: str) -> str:
    """Passwort hashen wie in der Hauptanwendung"""
    return hashlib.sha256(password.encode()).hexdigest()

def reset_database():
    """Datenbank komplett zurücksetzen"""
    print("🔄 Starte Database Reset...")
    
    # 1. Alte Datenbank löschen
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
            print(f"✅ Alte Datenbank '{DB_PATH}' gelöscht")
        except PermissionError:
            print(f"❌ FEHLER: Kann '{DB_PATH}' nicht löschen!")
            print("💡 Tipp: Stoppe den FastAPI-Server (Strg+C) und versuche es erneut")
            return False
        except Exception as e:
            print(f"❌ Unerwarteter Fehler: {e}")
            return False
    else:
        print(f"ℹ️  Datei '{DB_PATH}' existierte nicht")
    
    # 2. Neue Datenbank erstellen
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Users-Tabelle (genau wie in app.py)
        cursor.execute("""
            CREATE TABLE users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0
            )
        """)
        print("✅ Users-Tabelle erstellt")
        
        # Chat-History-Tabelle
        cursor.execute("""
            CREATE TABLE chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (username) REFERENCES users (username)
            )
        """)
        print("✅ Chat-History-Tabelle erstellt")
        
        # Standard Admin-User
        admin_hash = hash_password("admin")
        cursor.execute("""
            INSERT INTO users (username, password, question, answer, is_admin) 
            VALUES (?, ?, ?, ?, 1)
        """, ("admin", admin_hash, "Default Admin Question", "admin"))
        print("✅ Admin-User erstellt (admin/admin)")
        
        # Test-User für Tests
        test_hash = hash_password("test123")
        cursor.execute("""
            INSERT INTO users (username, password, question, answer) 
            VALUES (?, ?, ?, ?)
        """, ("testuser", test_hash, "Lieblingsfarbe?", "blau"))
        print("✅ Test-User erstellt (testuser/test123)")
        
        conn.commit()
        conn.close()
        
        return True
        
    except Exception as e:
        print(f"❌ Fehler beim Erstellen der Datenbank: {e}")
        return False

def verify_database():
    """Überprüft ob die Datenbank korrekt erstellt wurde"""
    if not os.path.exists(DB_PATH):
        print("❌ Datenbank wurde nicht erstellt!")
        return False
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Benutzer-Anzahl prüfen
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        
        # Admin-User prüfen
        cursor.execute("SELECT username FROM users WHERE is_admin = 1")
        admin = cursor.fetchone()
        
        # Tabellen-Struktur prüfen
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        table_names = [table[0] for table in tables]
        
        conn.close()
        
        print(f"✅ Verifikation erfolgreich:")
        print(f"   📊 {user_count} Benutzer erstellt")
        print(f"   👑 Admin: {admin[0] if admin else 'FEHLER - KEIN ADMIN!'}")
        print(f"   🗃️  Tabellen: {', '.join(table_names)}")
        
        return True
        
    except Exception as e:
        print(f"❌ Fehler bei Verifikation: {e}")
        return False

def show_login_info():
    """Zeigt die Standard-Login-Daten"""
    print("\n" + "="*50)
    print("🔑 LOGIN-DATEN:")
    print("="*50)
    print("👤 Admin-Account:")
    print("   Benutzername: admin")
    print("   Passwort:     admin")
    print("")
    print("👤 Test-Account:")
    print("   Benutzername: testuser")
    print("   Passwort:     test123")
    print("   Sicherheitsfrage: Lieblingsfarbe?")
    print("   Antwort: blau")
    print("="*50)
    print("🚀 Starte den Server: python app.py")
    print("🌐 Dann öffne: http://localhost:8000")

def main():
    """Hauptfunktion"""
    print("🗃️  KI-Chat Database Reset Tool")
    print("="*40)
    
    # Warnung
    print("⚠️  WARNUNG: Alle Benutzer und Chat-Verläufe werden gelöscht!")
    
    # Bestätigung
    confirm = input("\n❓ Fortfahren? (ja/nein): ").lower().strip()
    if confirm not in ['ja', 'j', 'yes', 'y']:
        print("❌ Abgebrochen")
        return
    
    # Reset durchführen
    if reset_database():
        print("\n🎉 Database Reset erfolgreich!")
        
        # Verifikation
        if verify_database():
            show_login_info()
        else:
            print("❌ Verifikation fehlgeschlagen!")
    else:
        print("\n💥 Database Reset fehlgeschlagen!")
        print("💡 Stelle sicher, dass der FastAPI-Server nicht läuft")

if __name__ == "__main__":
    main()