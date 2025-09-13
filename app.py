# ──────────────────────────────
# Rate Limiting & Session Management
# ──────────────────────────────
def load_rate_limits():
    """Lädt Rate-Limit-Daten"""
    if os.path.exists(RATE_LIMIT_FILE):
        try:
            with open(RATE_LIMIT_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_rate_limits(limits):
    """Speichert Rate-Limit-Daten"""
    with open(RATE_LIMIT_FILE, 'w') as f:
        json.dump(limits, f)

def check_rate_limit(username: str) -> bool:
    """Prüft ob User Rate-Limit erreicht hat"""
    limits = load_rate_limits()
    current_time = time()
    
    user_data = limits.get(username, {"messages": [], "last_reset": current_time})
    
    hour_ago = current_time - 3600
    user_data["messages"] = [msg_time for msg_time in user_data["messages"] if msg_time > hour_ago]
    
    if len(user_data["messages"]) >= MESSAGES_PER_HOUR:
        return False
    
    user_data["messages"].append(current_time)
    limits[username] = user_data
    save_rate_limits(limits)
    
    return True

def update_session_activity(request: Request):
    """Aktualisiert die letzte Aktivität in der Session"""
    request.session["last_activity"] = time()

def check_session_timeout(request: Request) -> bool:
    """Prüft ob Session abgelaufen ist"""
    last_activity = request.session.get("last_activity")
    if not last_activity:
        return True
    
    return (time() - last_activity) > SESSION_TIMEOUT_SECONDS

def require_active_session(request: Request):
    """Middleware-ähnliche Funktion für Session-Check"""
    if not request.session.get("username"):
        return RedirectResponse("/", status_code=302)
    
    if check_session_timeout(request):
        request.session.clear()
        return RedirectResponse("/?timeout=1", status_code=302)
    
    update_session_activity(request)
    return None

def is_admin(request: Request) -> bool:
    username = request.session.get("username")
    if username:
        user = get_user(username)
        return user and user["is_admin"]
    return False

def admin_redirect_guard(request: Request):
    if not is_admin(request):
        return RedirectResponse("/", status_code=302)
    return None

# ──────────────────────────────
# Validation Helper
# ──────────────────────────────
def validate_password_strength(password: str) -> tuple[bool, str]:
    """Validiert Passwort-Stärke"""
    if len(password) < 8:
        return False, "Passwort muss mindestens 8 Zeichen haben"
    
    if not any(c.isupper() for c in password):
        return False, "Passwort muss mindestens einen Großbuchstaben enthalten"
    
    if not any(c.islower() for c in password):
        return False, "Passwort muss mindestens einen Kleinbuchstaben enthalten"
    
    if not any(c.isdigit() for c in password):
        return False, "Passwort muss mindestens eine Zahl enthalten"
    
    return True, "Passwort ist sicher"

def validate_username(username: str) -> tuple[bool, str]:
    """Validiert Username"""
    if len(username) < 3:
        return False, "Benutzername muss mindestens 3 Zeichen haben"
    
    if len(username) > 20:
        return False, "Benutzername darf maximal 20 Zeichen haben"
    
    if not username.replace('_', '').replace('-', '').isalnum():
        return False, "Benutzername darf nur Buchstaben, Zahlen, _ und - enthalten"
    
    return True, "Benutzername ist gültig"

# ──────────────────────────────
# Routes - Auth
# ──────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    request.session.clear()
    timeout = request.query_params.get("timeout")
    return templates.TemplateResponse("login.html", {
        "request": request,
        "timeout_message": "Deine Session ist abgelaufen. Bitte melde dich erneut an." if timeout else None
    })

@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if check_login(username, password):
        user = get_user(username)
        if user["is_blocked"]:
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "Du wurdest vom Admin gesperrt."
            })
        
        request.session["username"] = username
        request.session["csrf_token"] = generate_csrf_token()
        update_session_activity(request)
        
        if user["is_admin"]:
            return RedirectResponse("/admin", status_code=302)
        else:
            return RedirectResponse("/chat", status_code=302)
    
    return templates.TemplateResponse("login.html", {
        "request": request, 
        "error": "Falsche Anmeldedaten"
    })

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    csrf_token = generate_csrf_token()
    return templates.TemplateResponse("register.html", {
        "request": request,
        "csrf_token": csrf_token
    })

@app.post("/register", response_class=HTMLResponse)
async def register(request: Request, 
                  username: str = Form(...), 
                  password: str = Form(...), 
                  question: str = Form(...), 
                  answer: str = Form(...),
                  csrf_token: str = Form(...)):
    
    # Validation
    username_valid, username_msg = validate_username(username)
    password_valid, password_msg = validate_password_strength(password)
    
    if not username_valid:
        return templates.TemplateResponse("register.html", {
            "request": request, 
            "error": username_msg,
            "csrf_token": generate_csrf_token()
        })
    
    if not password_valid:
        return templates.TemplateResponse("register.html", {
            "request": request, 
            "error": password_msg,
            "csrf_token": generate_csrf_token()
        })
    
    if save_user(username, password, question, answer):
        return RedirectResponse("/", status_code=302)
    
    return templates.TemplateResponse("register.html", {
        "request": request, 
        "error": "Benutzer existiert bereits",
        "csrf_token": generate_csrf_token()
    })

@app.get("/reset", response_class=HTMLResponse)
async def reset_page(request: Request):
    return templates.TemplateResponse("reset.html", {
        "request": request, 
        "error": "", 
        "success": "",
        "csrf_token": generate_csrf_token()
    })

@app.post("/reset", response_class=HTMLResponse)
async def reset_post(request: Request, 
                    username: str = Form(...), 
                    answer: str = Form(...), 
                    new_password: str = Form(...),
                    csrf_token: str = Form(...)):
    
    password_valid, password_msg = validate_password_strength(new_password)
    if not password_valid:
        return templates.TemplateResponse("reset.html", {
            "request": request, 
            "error": password_msg, 
            "success": "",
            "csrf_token": generate_csrf_token()
        })
    
    if verify_security_answer(username, answer):
        reset_password(username, new_password)
        return RedirectResponse("/", status_code=302)
    
    return templates.TemplateResponse("reset.html", {
        "request": request, 
        "error": "Antwort falsch", 
        "success": "",
        "csrf_token": generate_csrf_token()
    })

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)

# ──────────────────────────────
# Routes - Chat (with Subscription Limits)
# ──────────────────────────────
@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    username = request.session.get("username")
    history = get_user_history(username)
    tier = get_user_subscription(username)
    theme = get_user_theme(username)
    
    return templates.TemplateResponse("chat.html", {
        "request": request, 
        "username": username,
        "chat_history": history,
        "session_timeout_minutes": SESSION_TIMEOUT_MINUTES,
        "subscription_tier": tier.value,
        "theme": theme,
        "csrf_token": request.session.get("csrf_token", generate_csrf_token())
    })

@app.post("/chat")
async def chat_with_subscription_limits(req: Request):
    redirect = require_active_session(req)
    if redirect:
        return {"reply": "Session abgelaufen. Bitte neu anmelden.", "redirect": "/"}
    
    username = req.session.get("username")
    
    if not check_daily_limit(username, "messages"):
        tier = get_user_subscription(username)
        limit = SUBSCRIPTION_LIMITS[tier]["messages_per_day"]
        return {
            "reply": f"Tägliches Limit erreicht ({limit} Nachrichten). Upgrade auf Premium für mehr!",
            "upgrade_needed": True,
            "current_tier": tier.value
        }
    
    if not check_rate_limit(username):
        return {"reply": f"Rate-Limit erreicht! Maximal {MESSAGES_PER_HOUR} Nachrichten pro Stunde."}
    
    data = await req.json()
    user_message = data.get("message", "")
    
    if not user_message.strip():
        return {"reply": "Leere Nachricht."}
    
    save_user_history(username, "user", user_message)
    
    history = get_user_history(username)
    tier = get_user_subscription(username)
    max_context = SUBSCRIPTION_LIMITS[tier]["max_context_length"]
    limited_history = history[-max_context*2:] if max_context > 0 else history
    
    user_persona = get_user_persona(username)
    
    available_personas = get_available_personas(username)
    if user_persona not in available_personas:
        user_persona = "standard"
    
    try:
        raw_response = get_response_with_context(user_message, limited_history, user_persona)
        
        if check_feature_access(username, "markdown"):
            rendered_response = render_markdown_simple(raw_response)
        else:
            rendered_response = raw_response
        
        save_user_history(username, "assistant", raw_response)
        
        track_user_action(username, "chat_message", {"persona": user_persona})
        
        return {
            "reply": rendered_response,
            "raw_reply": raw_response if check_feature_access(username, "tts") else None,
            "subscription_tier": tier.value
        }
        
    except Exception as e:
        logger.error(f"Chat error for {username}: {str(e)}")
        return {"reply": "Ein Fehler ist aufgetreten. Versuche es erneut."}

@app.post("/chat/clear-history")
async def clear_user_history_with_tracking(request: Request):
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    username = request.session.get("username")
    
    delete_user_history(username)
    
    track_user_action(username, "feature_used", {"feature": "clear_history"})
    
    logger.info(f"[USER] {username} hat seinen Chat-Verlauf gelöscht")
    
    return RedirectResponse("/chat", status_code=302)

# ──────────────────────────────
# Routes - Chat Export
# ──────────────────────────────
@app.get("/chat/export", response_class=HTMLResponse)
async def export_chat_page(request: Request):
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    username = request.session.get("username")
    
    return templates.TemplateResponse("chat_export.html", {
        "request": request,
        "username": username,
        "csrf_token": request.session.get("csrf_token", generate_csrf_token())
    })

@app.post("/chat/export")
async def export_chat_download(request: Request, 
                              format: str = Form(...),
                              csrf_token: str = Form(...)):
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    username = request.session.get("username")
    
    if not verify_csrf_token(csrf_token, request.session.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    if username != "admin":
        toggle_user_block(username)
        logger.info(f"[ADMIN] User blockiert/freigeschaltet: {username}")
    
    return RedirectResponse("/admin", status_code=302)

@app.get("/admin/history/{username}", response_class=HTMLResponse)
async def view_user_history(request: Request, username: str):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    history = get_user_history(username)
    
    formatted = "<br><br>".join(
        f"<b>{html.escape(str(msg.get('role', 'unknown')))}:</b><br>{html.escape(str(msg.get('content', '')))}"
        for msg in history
    )
    
    body = f"""
    <html><body style="font-family: Arial; padding: 20px;">
    <h1>Chat-Verlauf von {html.escape(username)}</h1>
    <div style="background: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
        {formatted or '— Kein Verlauf vorhanden —'}
    </div>
    <a href='/admin' style="color: blue;">🔙 Zurück zum Admin-Panel</a>
    </body></html>
    """
    return HTMLResponse(body)

@app.post("/admin/delete-history")
async def delete_history(request: Request, 
                        username: str = Form(...),
                        csrf_token: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    if not verify_csrf_token(csrf_token, request.session.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    delete_user_history(username)
    logger.info(f"[ADMIN] Chat-Verlauf gelöscht: {username}")
    
    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/delete-user")
async def delete_user(request: Request, 
                     username: str = Form(...),
                     csrf_token: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    if not verify_csrf_token(csrf_token, request.session.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    if username != "admin":
        delete_user_completely(username)
        logger.info(f"[ADMIN] User gelöscht: {username}")
    
    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/change-user-password")
async def change_user_password(request: Request, 
                              username: str = Form(...), 
                              new_password: str = Form(...),
                              csrf_token: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    if not verify_csrf_token(csrf_token, request.session.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    password_valid, password_msg = validate_password_strength(new_password)
    if not password_valid:
        users = get_all_users()
        return templates.TemplateResponse("admin_users.html", {
            "request": request,
            "users": users,
            "error": password_msg,
            "csrf_token": request.session.get("csrf_token", generate_csrf_token())
        })
    
    if username != "admin":
        reset_password(username, new_password)
        logger.info(f"[ADMIN] Passwort geändert für: {username}")
    
    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/change-password")
async def change_admin_password(request: Request, 
                               old_password: str = Form(...), 
                               new_password: str = Form(...),
                               csrf_token: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    if not verify_csrf_token(csrf_token, request.session.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    password_valid, password_msg = validate_password_strength(new_password)
    if not password_valid:
        users = get_all_users()
        return templates.TemplateResponse("admin_users.html", {
            "request": request,
            "users": users,
            "error": password_msg,
            "csrf_token": request.session.get("csrf_token", generate_csrf_token())
        })
    
    admin_user = get_user("admin")
    if admin_user and hmac.compare_digest(admin_user["password"], hash_password(old_password)):
        reset_password("admin", new_password)
        logger.info("[ADMIN] Admin-Passwort geändert")
        return RedirectResponse("/admin", status_code=302)
    else:
        users = get_all_users()
        return templates.TemplateResponse("admin_users.html", {
            "request": request,
            "users": users,
            "error": "Altes Passwort ist falsch",
            "csrf_token": request.session.get("csrf_token", generate_csrf_token())
        })

@app.get("/admin/export-csv")
async def export_csv():
    users = get_all_users()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Benutzername", "Passwort-Hash", "Frage", "Antwort", "Admin", "Blockiert", "Subscription", "Theme"])
    
    for name, data in users.items():
        writer.writerow([
            name,
            data.get("password", ""),
            data.get("question", ""),
            data.get("answer", ""),
            "Ja" if data.get("is_admin") else "Nein",
            "Ja" if data.get("blocked") else "Nein",
            data.get("subscription", "free"),
            data.get("theme", "dark")
        ])
    
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=users.csv"}
    )

# ──────────────────────────────
# API Routes - Session Info
# ──────────────────────────────
@app.get("/api/session-info")
async def session_info(request: Request):
    if not request.session.get("username"):
        return {"active": False}
    
    last_activity = request.session.get("last_activity", time())
    remaining_seconds = max(0, SESSION_TIMEOUT_SECONDS - (time() - last_activity))
    username = request.session.get("username")
    
    return {
        "active": True,
        "username": username,
        "remaining_minutes": int(remaining_seconds / 60),
        "remaining_seconds": int(remaining_seconds),
        "subscription_tier": get_user_subscription(username).value if username else "free",
        "theme": get_user_theme(username) if username else "dark"
    }

# ──────────────────────────────
# Middleware
# ──────────────────────────────
@app.middleware("http")
async def add_template_context(request: Request, call_next):
    response = await call_next(request)
    
    if request.session.get("username"):
        username = request.session.get("username")
        theme = get_user_theme(username)
        if "csrf_token" not in request.session:
            request.session["csrf_token"] = generate_csrf_token()
    
    return response

# ──────────────────────────────
# Startup Event
# ──────────────────────────────
@app.on_event("startup")
def startup():
    init_db()
    init_api_keys_db()
    logger.info("[STARTUP] KI-Chat mit allen Features gestartet")
    logger.info("[STARTUP] Features: CSRF-Schutz, Chat-Export, Dark Mode, API-Keys in DB")
    logger.info("[STARTUP] Subscription-Tiers, Analytics-System, Erweiterte Validierung")
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    try:
        if format == "txt":
            content = export_user_chat_txt(username)
            return Response(
                content=content.encode('utf-8'),
                media_type="text/plain",
                headers={"Content-Disposition": f"attachment; filename={username}_chat.txt"}
            )
        
        elif format == "json":
            content = json.dumps(export_user_chat_json(username), indent=2, ensure_ascii=False)
            return Response(
                content=content.encode('utf-8'),
                media_type="application/json", 
                headers={"Content-Disposition": f"attachment; filename={username}_chat.json"}
            )
        
        elif format == "zip":
            zip_data = create_user_export_package(username)
            return Response(
                content=zip_data,
                media_type="application/zip",
                headers={"Content-Disposition": f"attachment; filename={username}_export.zip"}
            )
    
    except Exception as e:
        logger.error(f"Export error for {username}: {str(e)}")
        return templates.TemplateResponse("chat_export.html", {
            "request": request,
            "username": username,
            "error": "Export fehlgeschlagen",
            "csrf_token": request.session.get("csrf_token", generate_csrf_token())
        })

# ──────────────────────────────
# Routes - Theme Management
# ──────────────────────────────
@app.post("/settings/theme")
async def change_theme(request: Request, theme: str = Form(...)):
    redirect = require_active_session(request)
    if redirect:
        return {"success": False, "error": "Session expired"}
    
    username = request.session.get("username")
    
    if theme in ["dark", "light"]:
        set_user_theme(username, theme)
        
        track_user_action(username, "feature_used", {"feature": "theme_change", "theme": theme})
        
        return {"success": True, "theme": theme}
    
    return {"success": False, "error": "Invalid theme"}

# ──────────────────────────────
# Routes - Persona (with Subscription)
# ──────────────────────────────
@app.get("/persona", response_class=HTMLResponse)
async def persona_settings_with_subscription(request: Request):
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    username = request.session.get("username")
    current_persona = get_user_persona(username)
    available_personas = get_available_personas(username)
    tier = get_user_subscription(username)
    theme = get_user_theme(username)
    
    return templates.TemplateResponse("persona.html", {
        "request": request,
        "username": username, 
        "personas": available_personas,
        "all_personas": PERSONAS,
        "current_persona": current_persona,
        "subscription_tier": tier.value,
        "theme": theme,
        "csrf_token": request.session.get("csrf_token", generate_csrf_token())
    })

@app.post("/persona")
async def set_persona_with_tracking(request: Request, 
                                   persona: str = Form(...),
                                   csrf_token: str = Form(...)):
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    if not verify_csrf_token(csrf_token, request.session.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    username = request.session.get("username")
    available_personas = get_available_personas(username)
    
    if persona in available_personas:
        save_user_persona(username, persona)
        
        track_user_action(username, "feature_used", {"feature": "persona_change", "persona": persona})
        
        logger.info(f"[PERSONA] {username} wählte Persona: {persona}")
    
    return RedirectResponse("/chat", status_code=302)

# ──────────────────────────────
# Routes - Subscription Management
# ──────────────────────────────
@app.get("/subscription", response_class=HTMLResponse)
async def subscription_page(request: Request):
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    username = request.session.get("username")
    current_tier = get_user_subscription(username)
    theme = get_user_theme(username)
    
    analytics = load_analytics()
    user_data = analytics["users"].get(username, {})
    
    return templates.TemplateResponse("subscription.html", {
        "request": request,
        "username": username,
        "current_tier": current_tier.value,
        "limits": SUBSCRIPTION_LIMITS[current_tier],
        "usage": {
            "messages_today": user_data.get("messages_today", 0),
            "total_messages": user_data.get("total_messages", 0),
            "api_calls_today": user_data.get("api_calls_today", 0)
        },
        "tiers": {
            "free": SUBSCRIPTION_LIMITS[SubscriptionTier.FREE],
            "premium": SUBSCRIPTION_LIMITS[SubscriptionTier.PREMIUM], 
            "enterprise": SUBSCRIPTION_LIMITS[SubscriptionTier.ENTERPRISE]
        },
        "theme": theme,
        "csrf_token": request.session.get("csrf_token", generate_csrf_token())
    })

@app.post("/subscription/upgrade")
async def upgrade_subscription(request: Request, 
                              tier: str = Form(...),
                              csrf_token: str = Form(...)):
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    if not verify_csrf_token(csrf_token, request.session.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    username = request.session.get("username")
    
    try:
        new_tier = SubscriptionTier(tier)
    except ValueError:
        return RedirectResponse("/subscription?error=invalid_tier", status_code=302)
    
    set_user_subscription(username, new_tier)
    
    track_user_action(username, "feature_used", {"feature": "subscription_upgrade", "tier": tier})
    
    logger.info(f"[SUBSCRIPTION] {username} upgraded to {tier}")
    
    return RedirectResponse("/subscription?success=upgraded", status_code=302)

# ──────────────────────────────
# Routes - API Management
# ──────────────────────────────
@app.post("/api/generate-key")
async def generate_api_key_route_db(request: Request):
    redirect = require_active_session(request)
    if redirect:
        return {"error": "Not authenticated", "status_code": 401}
    
    username = request.session.get("username")
    
    existing_keys = get_user_api_keys(username)
    if len(existing_keys) >= 5:
        return {"error": "Maximum number of API keys reached (5)", "status_code": 429}
    
    api_key = f"ki-chat-{uuid.uuid4().hex[:16]}"
    
    save_api_key_to_db(api_key, username)
    
    logger.info(f"[API] API-Key generiert für {username}")
    
    return {
        "api_key": api_key,
        "usage": "Füge 'X-API-Key: {api_key}' zu deinen Request-Headers hinzu",
        "endpoints": {
            "chat": "/api/v1/chat",
            "models": "/api/v1/models", 
            "usage": "/api/v1/usage"
        },
        "existing_keys": len(existing_keys) + 1
    }

@app.get("/api/keys", response_class=HTMLResponse)
async def manage_api_keys(request: Request):
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    username = request.session.get("username")
    api_keys = get_user_api_keys(username)
    theme = get_user_theme(username)
    
    return templates.TemplateResponse("api_keys.html", {
        "request": request,
        "username": username,
        "api_keys": api_keys,
        "theme": theme,
        "csrf_token": request.session.get("csrf_token", generate_csrf_token())
    })

@app.get("/api/v1/models")
async def list_models(x_api_key: Optional[str] = Header(None)):
    if not x_api_key or not validate_api_key_db(x_api_key):
        return {"error": "Invalid API key", "status_code": 401}
    
    track_api_usage_db(x_api_key)
    
    return {
        "models": [
            {
                "id": "llama3.2",
                "name": "Llama 3.2",
                "description": "Schnelles lokales Modell",
                "max_tokens": 4096
            },
            {
                "id": "gpt-3.5-turbo", 
                "name": "GPT-3.5 Turbo",
                "description": "OpenAI Modell (API-Key erforderlich)",
                "max_tokens": 4096
            }
        ]
    }

@app.post("/api/v1/chat")
async def api_chat_with_limits(
    request: Request,
    x_api_key: Optional[str] = Header(None)
):
    if not x_api_key:
        return {"error": "API key required", "status_code": 401}
    
    user_info = validate_api_key_db(x_api_key)
    if not user_info:
        return {"error": "Invalid API key", "status_code": 401}
    
    username = user_info["username"]
    
    if not check_daily_limit(username, "api_calls"):
        tier = get_user_subscription(username)
        limit = SUBSCRIPTION_LIMITS[tier]["api_calls_per_day"]
        return {
            "error": f"Daily API limit exceeded ({limit} calls)",
            "status_code": 429,
            "upgrade_url": "/subscription"
        }
    
    track_api_usage_db(x_api_key)
    
    data = await request.json()
    message = data.get("message", "")
    persona = data.get("persona", "standard")
    model = data.get("model", "llama3.2")
    include_context = data.get("include_context", True)
    
    if not message.strip():
        return {"error": "Message cannot be empty", "status_code": 400}
    
    available_personas = get_available_personas(username)
    if persona not in available_personas:
        return {
            "error": f"Persona '{persona}' not available in your subscription",
            "available_personas": list(available_personas.keys()),
            "status_code": 403
        }
    
    try:
        if include_context:
            history = get_user_history(username)
            tier = get_user_subscription(username)
            max_context = SUBSCRIPTION_LIMITS[tier]["max_context_length"]
            limited_history = history[-max_context*2:] if max_context > 0 else history
            
            response_text = get_response_with_context(message, limited_history, persona)
            
            save_user_history(username, "user", message)
            save_user_history(username, "assistant", response_text)
        else:
            messages = [
                {"role": "system", "content": PERSONAS.get(persona, PERSONAS["standard"])["system_prompt"]},
                {"role": "user", "content": message}
            ]
            response_text = get_response_with_messages(messages)
        
        track_user_action(username, "api_call", {"persona": persona})
        
        return {
            "message": response_text,
            "model": model,
            "persona": persona,
            "tokens_used": len(response_text.split()),
            "subscription_tier": get_user_subscription(username).value,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"[API] Error for {username}: {str(e)}")
        return {"error": "Internal server error", "status_code": 500}

@app.get("/api/v1/usage")
async def api_usage_stats(x_api_key: Optional[str] = Header(None)):
    if not x_api_key or not validate_api_key_db(x_api_key):
        return {"error": "Invalid API key", "status_code": 401}
    
    user_info = validate_api_key_db(x_api_key)
    username = user_info["username"]
    tier = get_user_subscription(username)
    
    return {
        "username": username,
        "api_key": x_api_key[:8] + "...",
        "requests_today": user_info["requests_today"],
        "total_requests": user_info["total_requests"],
        "subscription_tier": tier.value,
        "created_at": user_info["created_at"],
        "rate_limit": {
            "requests_per_hour": MESSAGES_PER_HOUR,
            "requests_per_day": SUBSCRIPTION_LIMITS[tier]["api_calls_per_day"]
        }
    }

# ──────────────────────────────
# Routes - Admin Panel (Extended)
# ──────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    users = get_all_users()
    
    total_users = len(users)
    blocked_users = sum(1 for user in users.values() if user.get("blocked", False))
    premium_users = sum(1 for user in users.values() if user.get("subscription", "free") != "free")
    
    return templates.TemplateResponse("admin_users.html", {
        "request": request, 
        "users": users,
        "stats": {
            "total_users": total_users,
            "blocked_users": blocked_users,
            "premium_users": premium_users,
            "free_users": total_users - premium_users
        },
        "csrf_token": request.session.get("csrf_token", generate_csrf_token())
    })

@app.post("/admin/set-subscription")
async def admin_set_subscription(request: Request, 
                                username: str = Form(...), 
                                subscription: str = Form(...),
                                csrf_token: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    if not verify_csrf_token(csrf_token, request.session.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    try:
        tier = SubscriptionTier(subscription)
        set_user_subscription(username, tier)
        logger.info(f"[ADMIN] Subscription für {username} auf {subscription} gesetzt")
    except ValueError:
        logger.error(f"[ADMIN] Ungültige Subscription: {subscription}")
    
    return RedirectResponse("/admin", status_code=302)

@app.get("/admin/analytics", response_class=HTMLResponse)
async def analytics_dashboard(request: Request):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    analytics = load_analytics()
    
    last_7_days = []
    for i in range(7):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        last_7_days.append(date)
    
    daily_stats = []
    for date in reversed(last_7_days):
        day_data = analytics["daily"].get(date, {})
        daily_stats.append({
            "date": date,
            "messages": day_data.get("total_messages", 0),
            "users": len(day_data.get("unique_users", [])),
            "top_persona": max(day_data.get("personas_used", {"standard": 0}).items(), 
                              key=lambda x: x[1], default=("standard", 0))[0]
        })
    
    top_users = sorted(
        [(username, data["total_messages"]) for username, data in analytics["users"].items()],
        key=lambda x: x[1], 
        reverse=True
    )[:10]
    
    return templates.TemplateResponse("analytics.html", {
        "request": request,
        "daily_stats": daily_stats,
        "top_users": top_users,
        "total_users": len(analytics["users"]),
        "total_messages": sum(data["total_messages"] for data in analytics["users"].values()),
        "personas_usage": analytics["daily"].get(datetime.now().strftime("%Y-%m-%d"), {}).get("personas_used", {}),
        "csrf_token": request.session.get("csrf_token", generate_csrf_token())
    })

@app.post("/admin/toggle-block")
async def toggle_block_user(request: Request, 
                           username: str = Form(...),
                           csrf_token: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    if not verify_csrf_token(csrf_token, request.session.get("csrf_token")):from fastapi import FastAPI, Request, Form, HTTPException, Header
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv
from typing import Optional
from enum import Enum
import os
import logging
import sqlite3
import hashlib
import csv
import io
import tempfile
import html
import hmac
import json
import re
import uuid
import secrets
import zipfile
from datetime import datetime, timedelta
from time import time
from collections import defaultdict

from ollama_chat import get_response, get_response_with_messages

# ──────────────────────────────
# Setup & Configuration
# ──────────────────────────────
load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY", "supersecret")

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

# File paths
DB_PATH = "users.db"
RATE_LIMIT_FILE = "rate_limits.json"
ANALYTICS_FILE = "analytics.json"

# Settings
CHAT_TABLE_CREATED = False
MESSAGES_PER_HOUR = 50
SESSION_TIMEOUT_MINUTES = 30
SESSION_TIMEOUT_SECONDS = SESSION_TIMEOUT_MINUTES * 60

# API Key Management (deprecated - now in DB)
API_KEYS = {}

# ──────────────────────────────
# Subscription System
# ──────────────────────────────
class SubscriptionTier(Enum):
    FREE = "free"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"

# Persona-Definitionen
PERSONAS = {
    "standard": {
        "name": "Standard Assistent",
        "emoji": "🤖",
        "system_prompt": "Du bist ein hilfsfreundlicher KI-Assistent. Antworte auf Deutsch und sei sachlich aber freundlich."
    },
    "freundlich": {
        "name": "Freundlicher Helfer", 
        "emoji": "😊",
        "system_prompt": "Du bist ein sehr freundlicher und enthusiastischer Assistent. Verwende warme, ermutigende Worte und zeige echtes Interesse an den Fragen. Sei optimistisch und unterstützend."
    },
    "lustig": {
        "name": "Comedy Bot",
        "emoji": "😄", 
        "system_prompt": "Du bist ein humorvoller Assistent der gerne Witze macht und lustige Antworten gibt. Bleibe trotzdem hilfreich, aber bringe den User zum Lächeln. Verwende gelegentlich Wortwitz oder lustige Vergleiche."
    },
    "professionell": {
        "name": "Business Experte",
        "emoji": "👔",
        "system_prompt": "Du bist ein professioneller Berater mit Expertise in Business und Technik. Antworte präzise, strukturiert und sachlich. Nutze Fachbegriffe angemessen und gib konkrete Handlungsempfehlungen."
    },
    "lehrerin": {
        "name": "Geduldige Lehrerin", 
        "emoji": "👩‍🏫",
        "system_prompt": "Du bist eine geduldige Lehrerin die komplexe Themen einfach erklärt. Baue Erklärungen schrittweise auf, verwende Beispiele und frage nach ob alles verstanden wurde. Ermutige zum Lernen."
    },
    "kreativ": {
        "name": "Kreativer Geist",
        "emoji": "🎨", 
        "system_prompt": "Du bist ein kreativer Assistent voller Ideen und Inspiration. Denke um die Ecke, schlage ungewöhnliche Lösungen vor und bringe künstlerische Perspektiven ein. Sei experimentierfreudig."
    }
}

SUBSCRIPTION_LIMITS = {
    SubscriptionTier.FREE: {
        "messages_per_day": 50,
        "api_calls_per_day": 100,
        "personas": ["standard", "freundlich", "lustig"],
        "features": ["basic_chat", "history"],
        "max_context_length": 5
    },
    SubscriptionTier.PREMIUM: {
        "messages_per_day": 500,
        "api_calls_per_day": 1000,
        "personas": list(PERSONAS.keys()),
        "features": ["basic_chat", "history", "tts", "export", "advanced_personas", "markdown"],
        "max_context_length": 20
    },
    SubscriptionTier.ENTERPRISE: {
        "messages_per_day": -1,
        "api_calls_per_day": -1,
        "personas": list(PERSONAS.keys()),
        "features": ["all"],
        "max_context_length": 50
    }
}

# ──────────────────────────────
# CSRF Protection
# ──────────────────────────────
def generate_csrf_token():
    """Generiert CSRF-Token für Templates"""
    return secrets.token_urlsafe(32)

def verify_csrf_token(request_token: str, session_token: str) -> bool:
    """Verifiziert CSRF-Token"""
    return request_token == session_token and session_token is not None

# ──────────────────────────────
# Database Helper Functions
# ──────────────────────────────
def init_db():
    """Initialisiert die Datenbank mit allen nötigen Tabellen"""
    global CHAT_TABLE_CREATED
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Users-Tabelle (erweitert mit subscription und theme)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0,
            persona TEXT DEFAULT 'standard',
            subscription TEXT DEFAULT 'free',
            theme TEXT DEFAULT 'dark'
        )
    """)
    
    # Chat-Verlauf Tabelle
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (username) REFERENCES users (username)
        )
    """)
    
    # Admin-User erstellen falls nicht existiert
    cursor.execute("SELECT username FROM users WHERE username = ?", ("admin",))
    if not cursor.fetchone():
        admin_hash = hash_password("admin")
        cursor.execute("""
            INSERT INTO users (username, password, question, answer, is_admin, subscription) 
            VALUES (?, ?, ?, ?, 1, 'enterprise')
        """, ("admin", admin_hash, "Default Admin Question", "admin"))
        logger.info("[INIT] Admin-User erstellt (admin/admin)")
    
    conn.commit()
    conn.close()
    CHAT_TABLE_CREATED = True
    logger.info("[INIT] Datenbank initialisiert")

def init_api_keys_db():
    """Erstellt API-Keys Tabelle in Datenbank"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key TEXT UNIQUE NOT NULL,
            username TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_used DATETIME,
            requests_today INTEGER DEFAULT 0,
            total_requests INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT 1,
            FOREIGN KEY (username) REFERENCES users (username)
        )
    """)
    
    conn.commit()
    conn.close()

def hash_password(password: str) -> str:
    """Passwort hashen"""
    return hashlib.sha256(password.encode()).hexdigest()

def render_markdown_simple(text: str) -> str:
    """Einfaches Markdown-Rendering für Chat-Nachrichten"""
    text = html.escape(text)
    
    # Code-Blöcke (```code```)
    text = re.sub(
        r'```(\w+)?\n?(.*?)```', 
        r'<div class="code-block"><div class="code-header">\1</div><pre><code>\2</code></pre></div>', 
        text, 
        flags=re.DOTALL
    )
    
    # Inline-Code (`code`)
    text = re.sub(r'`([^`]+)`', r'<code class="inline-code">\1</code>', text)
    
    # Fett (**text**)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    
    # Kursiv (*text*)
    text = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', text)
    
    # Listen (- item)
    lines = text.split('\n')
    in_list = False
    result_lines = []
    
    for line in lines:
        if line.strip().startswith('- '):
            if not in_list:
                result_lines.append('<ul class="chat-list">')
                in_list = True
            result_lines.append(f'<li>{line.strip()[2:]}</li>')
        else:
            if in_list:
                result_lines.append('</ul>')
                in_list = False
            result_lines.append(line)
    
    if in_list:
        result_lines.append('</ul>')
    
    text = '\n'.join(result_lines)
    
    # Links [text](url)
    text = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)', 
        r'<a href="\2" target="_blank" class="chat-link">\1</a>', 
        text
    )
    
    # Zeilenumbrüche
    text = text.replace('\n', '<br>')
    
    return text

def get_user(username: str) -> dict:
    """User aus DB holen"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            "username": row[0],
            "password": row[1],
            "question": row[2],
            "answer": row[3],
            "is_admin": bool(row[4]),
            "is_blocked": bool(row[5]),
            "persona": row[6] if len(row) > 6 else "standard",
            "subscription": row[7] if len(row) > 7 else "free",
            "theme": row[8] if len(row) > 8 else "dark"
        }
    return None

def get_all_users() -> dict:
    """Alle User für Admin-Panel"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    rows = cursor.fetchall()
    conn.close()
    
    users = {}
    for row in rows:
        users[row[0]] = {
            "password": row[1],
            "question": row[2],
            "answer": row[3],
            "is_admin": bool(row[4]),
            "blocked": bool(row[5]),
            "subscription": row[7] if len(row) > 7 else "free",
            "theme": row[8] if len(row) > 8 else "dark"
        }
    return users

def save_user(username: str, password: str, question: str, answer: str) -> bool:
    """Neuen User registrieren"""
    if get_user(username):
        return False
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        password_hash = hash_password(password)
        cursor.execute("""
            INSERT INTO users (username, password, question, answer) 
            VALUES (?, ?, ?, ?)
        """, (username, password_hash, question, answer))
        conn.commit()
        logger.info(f"[REGISTER] Neuer User: {username}")
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def check_login(username: str, password: str) -> bool:
    """Login überprüfen"""
    user = get_user(username)
    if user:
        return user["password"] == hash_password(password)
    return False

def verify_security_answer(username: str, answer: str) -> bool:
    """Sicherheitsantwort prüfen"""
    user = get_user(username)
    if user:
        return user["answer"] == answer
    return False

def reset_password(username: str, new_password: str):
    """Passwort zurücksetzen"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    password_hash = hash_password(new_password)
    cursor.execute("UPDATE users SET password = ? WHERE username = ?", 
                   (password_hash, username))
    conn.commit()
    conn.close()

def get_user_history(username: str) -> list:
    """Chat-Verlauf aus DB laden"""
    if not CHAT_TABLE_CREATED:
        return []
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT role, content, timestamp FROM chat_history 
        WHERE username = ? ORDER BY timestamp ASC
    """, (username,))
    rows = cursor.fetchall()
    conn.close()
    
    return [{"role": row[0], "content": row[1], "timestamp": row[2]} for row in rows]

def save_user_history(username: str, role: str, content: str):
    """Chat-Nachricht in DB speichern"""
    if not CHAT_TABLE_CREATED:
        return
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO chat_history (username, role, content) 
        VALUES (?, ?, ?)
    """, (username, role, content))
    conn.commit()
    conn.close()

def delete_user_history(username: str):
    """Chat-Verlauf löschen"""
    if not CHAT_TABLE_CREATED:
        return
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_history WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def toggle_user_block(username: str):
    """User blockieren/entblockieren"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT is_blocked FROM users WHERE username = ?", (username,))
    current = cursor.fetchone()[0]
    new_status = 0 if current else 1
    cursor.execute("UPDATE users SET is_blocked = ? WHERE username = ?", 
                   (new_status, username))
    conn.commit()
    conn.close()

def delete_user_completely(username: str):
    """User und alle Daten löschen"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    cursor.execute("DELETE FROM chat_history WHERE username = ?", (username,))
    cursor.execute("DELETE FROM api_keys WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def get_user_persona(username: str) -> str:
    """Holt die gewählte Persona des Users"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT persona FROM users WHERE username = ?", (username,))
        result = cursor.fetchone()
        return result[0] if result and result[0] else "standard"
    except sqlite3.OperationalError:
        return "standard"
    finally:
        conn.close()

def save_user_persona(username: str, persona: str):
    """Speichert die gewählte Persona des Users"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    
    if 'persona' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN persona TEXT DEFAULT 'standard'")
    
    cursor.execute("UPDATE users SET persona = ? WHERE username = ?", (persona, username))
    conn.commit()
    conn.close()

def get_user_theme(username: str) -> str:
    """Holt Theme-Einstellung des Users"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT theme FROM users WHERE username = ?", (username,))
        result = cursor.fetchone()
        return result[0] if result and result[0] else "dark"
    except sqlite3.OperationalError:
        return "dark"
    finally:
        conn.close()

def set_user_theme(username: str, theme: str):
    """Setzt Theme-Einstellung für User"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    
    if 'theme' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN theme TEXT DEFAULT 'dark'")
    
    cursor.execute("UPDATE users SET theme = ? WHERE username = ?", (theme, username))
    conn.commit()
    conn.close()

def get_response_with_context(current_message: str, chat_history: list, persona: str = "standard") -> str:
    """Holt KI-Antwort mit Chat-Kontext und Persona"""
    messages = []
    
    persona_config = PERSONAS.get(persona, PERSONAS["standard"])
    messages.append({
        "role": "system", 
        "content": persona_config["system_prompt"]
    })
    
    for msg in chat_history[-20:]:
        if msg["role"] in ["user", "assistant"]:
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
    
    messages.append({
        "role": "user",
        "content": current_message
    })
    
    return get_response_with_messages(messages)

# ──────────────────────────────
# Subscription Management
# ──────────────────────────────
def get_user_subscription(username: str) -> SubscriptionTier:
    """Holt Subscription-Status eines Users"""
    user = get_user(username)
    if user:
        subscription = user.get("subscription", "free")
        try:
            return SubscriptionTier(subscription)
        except ValueError:
            return SubscriptionTier.FREE
    return SubscriptionTier.FREE

def set_user_subscription(username: str, tier: SubscriptionTier):
    """Setzt Subscription-Tier für User"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    
    if 'subscription' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN subscription TEXT DEFAULT 'free'")
    
    cursor.execute("UPDATE users SET subscription = ? WHERE username = ?", (tier.value, username))
    conn.commit()
    conn.close()

def check_feature_access(username: str, feature: str) -> bool:
    """Prüft ob User Zugriff auf Feature hat"""
    tier = get_user_subscription(username)
    allowed_features = SUBSCRIPTION_LIMITS[tier]["features"]
    
    return "all" in allowed_features or feature in allowed_features

def check_daily_limit(username: str, limit_type: str) -> bool:
    """Prüft tägliche Limits (Nachrichten, API-Calls)"""
    tier = get_user_subscription(username)
    limit = SUBSCRIPTION_LIMITS[tier].get(f"{limit_type}_per_day", 0)
    
    if limit == -1:
        return True
    
    analytics = load_analytics()
    today = datetime.now().strftime("%Y-%m-%d")
    user_data = analytics["users"].get(username, {})
    
    current_usage = user_data.get(f"{limit_type}_today", 0)
    return current_usage < limit

def get_available_personas(username: str) -> dict:
    """Gibt verfügbare Personas basierend auf Subscription zurück"""
    tier = get_user_subscription(username)
    allowed_personas = SUBSCRIPTION_LIMITS[tier]["personas"]
    
    return {key: value for key, value in PERSONAS.items() if key in allowed_personas}

# ──────────────────────────────
# Analytics System
# ──────────────────────────────
def load_analytics():
    """Lädt Analytics-Daten"""
    if os.path.exists(ANALYTICS_FILE):
        try:
            with open(ANALYTICS_FILE, 'r') as f:
                return json.load(f)
        except:
            return {"daily": {}, "users": {}, "features": {}, "api": {}}
    return {"daily": {}, "users": {}, "features": {}, "api": {}}

def save_analytics(data):
    """Speichert Analytics-Daten"""
    with open(ANALYTICS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def track_user_action(username: str, action: str, details: dict = None):
    """Trackt User-Aktionen für Analytics"""
    analytics = load_analytics()
    today = datetime.now().strftime("%Y-%m-%d")
    
    if today not in analytics["daily"]:
        analytics["daily"][today] = {
            "total_messages": 0,
            "unique_users": set(),
            "personas_used": defaultdict(int),
            "features_used": defaultdict(int)
        }
    
    if username not in analytics["users"]:
        analytics["users"][username] = {
            "first_seen": today,
            "last_seen": today,
            "total_messages": 0,
            "messages_today": 0,
            "api_calls_today": 0,
            "favorite_persona": "standard",
            "features_used": []
        }
    
    if analytics["users"][username].get("last_reset") != today:
        analytics["users"][username]["messages_today"] = 0
        analytics["users"][username]["api_calls_today"] = 0
        analytics["users"][username]["last_reset"] = today
    
    if action == "chat_message":
        analytics["daily"][today]["total_messages"] += 1
        analytics["daily"][today]["unique_users"].add(username)
        analytics["users"][username]["total_messages"] += 1
        analytics["users"][username]["messages_today"] += 1
        analytics["users"][username]["last_seen"] = today
        
        if details and "persona" in details:
            analytics["daily"][today]["personas_used"][details["persona"]] += 1
    
    elif action == "api_call":
        analytics["users"][username]["api_calls_today"] += 1
        if details and "persona" in details:
            analytics["daily"][today]["personas_used"][details["persona"]] += 1
    
    elif action == "feature_used":
        feature = details.get("feature", "unknown")
        analytics["daily"][today]["features_used"][feature] += 1
        if feature not in analytics["users"][username]["features_used"]:
            analytics["users"][username]["features_used"].append(feature)
    
    if isinstance(analytics["daily"][today]["unique_users"], set):
        analytics["daily"][today]["unique_users"] = list(analytics["daily"][today]["unique_users"])
    analytics["daily"][today]["personas_used"] = dict(analytics["daily"][today]["personas_used"])
    analytics["daily"][today]["features_used"] = dict(analytics["daily"][today]["features_used"])
    
    save_analytics(analytics)

# ──────────────────────────────
# API Key Management (Database)
# ──────────────────────────────
def save_api_key_to_db(api_key: str, username: str):
    """Speichert API-Key in Datenbank"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO api_keys (api_key, username, created_at) 
        VALUES (?, ?, ?)
    """, (api_key, username, datetime.now()))
    
    conn.commit()
    conn.close()

def get_api_key_from_db(api_key: str) -> dict:
    """Holt API-Key Info aus Datenbank"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT username, created_at, last_used, requests_today, total_requests, is_active 
        FROM api_keys WHERE api_key = ? AND is_active = 1
    """, (api_key,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            "username": row[0],
            "created_at": row[1],
            "last_used": row[2],
            "requests_today": row[3],
            "total_requests": row[4],
            "is_active": bool(row[5])
        }
    return None

def update_api_key_usage(api_key: str):
    """Aktualisiert API-Key Nutzungsstatistiken"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("""
        UPDATE api_keys 
        SET requests_today = CASE 
            WHEN date(last_used) != date('now') THEN 1 
            ELSE requests_today + 1 
        END,
        total_requests = total_requests + 1,
        last_used = ?
        WHERE api_key = ?
    """, (datetime.now(), api_key))
    
    conn.commit()
    conn.close()

def get_user_api_keys(username: str) -> list:
    """Holt alle API-Keys eines Users"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT api_key, created_at, last_used, requests_today, total_requests 
        FROM api_keys WHERE username = ? AND is_active = 1
        ORDER BY created_at DESC
    """, (username,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [{
        "api_key": row[0],
        "created_at": row[1],
        "last_used": row[2],
        "requests_today": row[3],
        "total_requests": row[4]
    } for row in rows]

def validate_api_key_db(api_key: str) -> Optional[dict]:
    """Validiert API-Key aus Datenbank"""
    return get_api_key_from_db(api_key)

def track_api_usage_db(api_key: str):
    """Trackt API-Nutzung in Datenbank"""
    update_api_key_usage(api_key)

# ──────────────────────────────
# Chat Export Functions
# ──────────────────────────────
def export_user_chat_txt(username: str) -> str:
    """Exportiert Chat-Verlauf als TXT"""
    history = get_user_history(username)
    
    txt_content = f"Chat-Verlauf für {username}\n"
    txt_content += f"Exportiert am: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
    txt_content += "=" * 50 + "\n\n"
    
    for msg in history:
        role = "Du" if msg["role"] == "user" else "KI-Assistent"
        timestamp = msg.get("timestamp", "Unbekannt")
        txt_content += f"[{timestamp}] {role}:\n{msg['content']}\n\n"
    
    return txt_content

def export_user_chat_json(username: str) -> dict:
    """Exportiert Chat-Verlauf als JSON"""
    history = get_user_history(username)
    user = get_user(username)
    
    return {
        "username": username,
        "export_date": datetime.now().isoformat(),
        "user_info": {
            "persona": user.get("persona", "standard") if user else "unknown",
            "subscription": user.get("subscription", "free") if user else "unknown"
        },
        "total_messages": len(history),
        "chat_history": history
    }

def create_user_export_package(username: str) -> bytes:
    """Erstellt komplettes Export-Paket als ZIP"""
    temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    
    with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
        txt_content = export_user_chat_txt(username)
        zipf.writestr(f"{username}_chat.txt", txt_content.encode('utf-8'))
        
        json_content = json.dumps(export_user_chat_json(username), indent=2, ensure_ascii=False)
        zipf.writestr(f"{username}_chat.json", json_content.encode('utf-8'))
        
        analytics = load_analytics()
        user_analytics = analytics.get("users", {}).get(username, {})
        if user_analytics:
            analytics_content = json.dumps(user_analytics, indent=2, ensure_ascii=False)
            zipf.writestr(f"{username}_analytics.json", analytics_content.encode('utf-8'))
    
    with open(temp_zip.name, 'rb') as f:
        zip_data = f.read()
    
    os.unlink(temp_zip.name)
    
    return zip_data

# ──────────────────────────────
# Rate Limiting & Session Management
# ──────────────────────────────