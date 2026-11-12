# Ortam değişkenleri ve yapılandırma

Uygulama ayarları Pydantic Settings aracılığıyla `.env` dosyasından veya süreç ortamından okunur. Ortam değişkeni adı, Python alanının büyük harfli biçimidir. Gerçek sırları `.env.example`, dokümantasyon, Git veya Docker imajına koymayın.

## Veritabanı ve kimlik doğrulama

| Değişken | Gereklilik / varsayılan | Açıklama |
|---|---|---|
| `DATABASE_URL` | Zorunlu | SQLAlchemy bağlantısı. PostgreSQL için `postgresql+psycopg://...` biçimi kullanılır; `postgresql://` biçimi de otomatik olarak psycopg sürücüsüne çevrilir. |
| `POSTGRES_DB` | Compose'da zorunlu | Yerel PostgreSQL veritabanı adı. |
| `POSTGRES_USER` | Compose'da zorunlu | Yerel PostgreSQL başlangıç kullanıcısı. |
| `POSTGRES_PASSWORD` | Compose'da zorunlu | Yerel PostgreSQL başlangıç parolası. İlk volume kurulurken uygulanır; mevcut volume'de parola değiştirmek DB rolünü kendiliğinden güncellemez. |
| `JWT_SECRET_KEY` | Zorunlu; en az 32 karakter | Access token imzalama anahtarı. |
| `JWT_ALGORITHM` | `HS256` | Kullanılan JWT algoritması; kod yalnızca HS256'yı kabul eder. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access token süresi (dakika). |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token süresi (gün). |
| `PASSWORD_RESET_EXPIRE_MINUTES` | `30` | Parola sıfırlama kodu süresi. |
| `EMAIL_VERIFICATION_EXPIRE_MINUTES` | `60` | E-posta doğrulama kodu süresi. |

Docker Compose backend için `DATABASE_URL`'i `db:5432` adresi ve `POSTGRES_*` değişkenleriyle oluşturur. Container içindeki `localhost` container'ın kendisini ifade ettiğinden, Compose backend ayarında DB host'u olarak kullanılmaz. Host'ta uygulamayı doğrudan çalıştırırken `localhost` kullanılabilir.

## Google, e-posta ve istemci

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `GOOGLE_CLIENT_ID` | Boş | Google OAuth istemci kimliği. |
| `GOOGLE_CLIENT_SECRET` | Boş | Google OAuth istemci sırrı. |
| `GOOGLE_REDIRECT_URI` | `http://127.0.0.1:8000/api/v1/auth/google/callback` | OAuth callback adresi; Google Console ile eşleşmelidir. |
| `SMTP_HOST` | Boş | E-posta sunucusu. |
| `SMTP_PORT` | `587` | SMTP portu. |
| `SMTP_USERNAME` | Boş | SMTP kullanıcı adı. |
| `SMTP_PASSWORD` | Boş | SMTP parolası. |
| `EMAIL_FROM` | Boş | Gönderen e-posta adresi. |
| `FRONTEND_URL` | `http://127.0.0.1:3000` | CORS izinli frontend origin'i ve frontend bağlantıları için temel URL. |
| `COOKIE_SECURE` | `false` | OAuth state cookie'sinde `Secure` bayrağı. HTTPS üretim ortamında etkinleştirin. |

SMTP ayarı eksik veya gönderim geçici olarak başarısız olduğunda kayıt ve sıfırlama endpoint'leri genel yanıtı korur; e-posta teslimi ayrıca SMTP ile doğrulanmalıdır.

## Dosya depolama

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `STORAGE_BACKEND` | `local` | `local` veya `cloudinary`. |
| `UPLOAD_DIR` | `uploads` | Yerel dosya saklama dizini. |
| `MAX_UPLOAD_MB` | `10` | PDF/TXT yükleme boyutu sınırı (MiB). |
| `CLOUDINARY_CLOUD_NAME` | Boş | Cloudinary cloud adı. |
| `CLOUDINARY_API_KEY` | Boş | Cloudinary API anahtarı. |
| `CLOUDINARY_API_SECRET` | Boş | Cloudinary API sırrı. |

## n8n ve AI limitleri

| Değişken | Varsayılan | Açıklama |
|---|---:|---|
| `N8N_BASE_URL` | Boş | n8n Cloud veya yerel n8n taban adresi. |
| `N8N_WEBHOOK_SECRET` | Boş | `X-Webhook-Secret` başlığında gönderilen sır. |
| `N8N_TIMEOUT_SECONDS` | `90` | Webhook isteği zaman aşımı. |
| `AI_MAX_INPUT_CHARS` | `30000` | Özet/quiz AI girdisi için karakter sınırı; uzun metin paragraf bazında örneklenir. |
| `AI_DAILY_LIMIT_FREE` | `3` | Ücretsiz plan için özet ve quiz üretiminin ortak 24 saatlik limiti. |
| `AI_DAILY_LIMIT_PLUS` | `30` | Plus plan için aynı ortak limit. |
| `AI_CHAT_DAILY_LIMIT_FREE` | `50` | Ücretsiz plan chat limiti; diğer AI üretimlerinden ayrıdır. |
| `AI_CHAT_DAILY_LIMIT_PLUS` | `500` | Plus plan chat limiti. |
| `CHAT_HISTORY_LIMIT` | `10` | n8n'e gönderilen en fazla geçmiş mesaj. |
| `CHAT_TOP_K` | `4` | Chat webhook payload'ındaki retrieval `top_k`. |
| `CHAT_MAX_NOTE_IDS` | `200` | Chat'e izinli kaynak olarak gönderilecek azami indeksli not ID'si. |
| `AI_SUMMARY_MAX_CARDS` | `15` | Özet çıktısında beklenen azami kart sayısı (1–50). |
| `AI_CARD_TARGET_CHARS` | `400` | İstenen özet kartı hedef karakter sayısı. |
| `AI_OUTPUT_LANGUAGE` | `tr` | Özet webhook dil alanı. |
| `INDEX_MAX_INPUT_CHARS` | `20000` | Not indeksleme metni karakter sınırı. |

n8n adresi HTTPS olmalıdır; `localhost`/loopback için HTTP kullanılabilir. Webhook yolları ve payload sözleşmeleri [mimari rehberinde](architecture.md) ve [API rehberinde](api.md) açıklanmıştır. Pinecone index ayarı n8n entegrasyonuna aittir; önerilen index 1024 boyut ve cosine metric kullanır.

## Quiz ve özet doğrulama sınırları

| Değişken | Varsayılan | Açıklama |
|---|---:|---|
| `SUMMARY_MAX_CHARS` | `1000` | Tek özet kartı içerik sınırı. |
| `QUIZ_QUESTION_TEXT_MAX_CHARS` | `1000` | Soru metni sınırı. |
| `QUIZ_OPTIONS_MIN_COUNT` | `2` | Soru başına en az seçenek. |
| `QUIZ_OPTIONS_MAX_COUNT` | `6` | Soru başına en çok seçenek. |
| `QUIZ_OPTION_MAX_CHARS` | `300` | Tek seçenek metni sınırı. |
| `QUIZ_EXPLANATION_MAX_CHARS` | `1500` | Açıklama metni sınırı. |
| `QUIZ_QUESTIONS_MIN_COUNT` | `1` | Manuel/AI quiz için genel alt sınır. |
| `QUIZ_QUESTIONS_MAX_COUNT` | `30` | Manuel/AI quiz için genel üst sınır. |

AI quiz üretim endpoint'i ayrıca `question_count` için 3–10 aralığını kabul eder. `.env.example` bütün uygulama ayarlarının yer tutuculu örneğidir; kendi ortam değerlerinizi orada tutmayın.
