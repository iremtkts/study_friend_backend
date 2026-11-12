# Veritabanı şeması

## ER diyagramı

İlişkiler SQLAlchemy modelleri ve Alembic migration'larından çıkarılmıştır. Opsiyonel FK'ler diyagramda `o|` ile gösterilir. `ai_jobs.result_id` UUID alanıdır ancak şu an FK değildir.

```mermaid
erDiagram
    USERS {
        uuid id PK
        string email
        string password_hash
        string full_name
        string role
        boolean is_active
        datetime email_verified_at
        string auth_provider
        string google_sub
    }
    REFRESH_TOKENS {
        uuid id PK
        uuid user_id FK
        string token_hash
        uuid family_id
        datetime expires_at
        datetime revoked_at
    }
    EMAIL_VERIFICATION_TOKENS {
        uuid id PK
        uuid user_id FK
        string token_hash
        datetime expires_at
        datetime used_at
        int attempt_count
    }
    PASSWORD_RESET_TOKENS {
        uuid id PK
        uuid user_id FK
        string token_hash
        datetime expires_at
        datetime used_at
        int attempt_count
    }
    MEMBERSHIPS {
        uuid id PK
        uuid user_id FK
        string plan
        string status
        datetime started_at
        datetime expires_at
    }
    CATEGORIES {
        uuid id PK
        uuid user_id FK
        string name
    }
    NOTES {
        uuid id PK
        uuid user_id FK
        uuid category_id FK
        string title
        string file_type
        string stored_filename
        text extracted_text
        string index_status
    }
    SUMMARIES {
        uuid id PK
        uuid user_id FK
        uuid note_id FK
        text content
        int position
        string source
    }
    CONVERSATIONS {
        uuid id PK
        uuid user_id FK
        uuid category_id FK
        uuid note_id FK
        string title
    }
    MESSAGES {
        uuid id PK
        uuid conversation_id FK
        string role
        text content
        jsonb sources
        string model_name
    }
    AI_JOBS {
        uuid id PK
        uuid user_id FK
        uuid note_id FK
        uuid conversation_id FK
        uuid result_id
        string job_type
        string status
        string error_code
        string model_name
        int result_count
    }
    QUIZZES {
        uuid id PK
        uuid user_id FK
        uuid category_id FK
        uuid note_id FK
        string title
        string source
    }
    QUIZ_QUESTIONS {
        uuid id PK
        uuid quiz_id FK
        int position
        text question_text
        jsonb options
        int correct_index
        text explanation
    }
    QUIZ_ATTEMPTS {
        uuid id PK
        uuid quiz_id FK
        uuid user_id FK
        string status
        int correct_count
        int total_questions
        decimal score_percent
    }
    QUIZ_ANSWERS {
        uuid id PK
        uuid attempt_id FK
        uuid question_id FK
        int selected_index
        boolean is_correct
    }
    STUDY_PLANS {
        uuid id PK
        uuid user_id FK
        date week_start
        string title
        string source
    }
    STUDY_PLAN_ITEMS {
        uuid id PK
        uuid plan_id FK
        int day_of_week
        time start_time
        int duration_minutes
        string topic
        uuid category_id FK
        uuid note_id FK
        boolean is_completed
    }

    USERS ||--o{ REFRESH_TOKENS : has
    USERS ||--o{ EMAIL_VERIFICATION_TOKENS : has
    USERS ||--o{ PASSWORD_RESET_TOKENS : has
    USERS ||--o{ MEMBERSHIPS : has
    USERS ||--o{ CATEGORIES : owns
    USERS ||--o{ NOTES : owns
    USERS ||--o{ SUMMARIES : owns
    USERS ||--o{ CONVERSATIONS : owns
    USERS ||--o{ AI_JOBS : owns
    USERS ||--o{ QUIZZES : owns
    USERS ||--o{ QUIZ_ATTEMPTS : takes
    USERS ||--o{ STUDY_PLANS : owns
    CATEGORIES ||--o{ NOTES : groups
    CATEGORIES ||--o{ CONVERSATIONS : groups
    CATEGORIES ||--o{ QUIZZES : groups
    CATEGORIES o|--o{ STUDY_PLAN_ITEMS : classifies
    NOTES ||--o{ SUMMARIES : summarizes
    NOTES o|--o{ CONVERSATIONS : scopes
    NOTES o|--o{ AI_JOBS : source
    NOTES o|--o{ QUIZZES : source
    NOTES o|--o{ STUDY_PLAN_ITEMS : references
    CONVERSATIONS ||--o{ MESSAGES : contains
    CONVERSATIONS o|--o{ AI_JOBS : tracks
    QUIZZES ||--o{ QUIZ_QUESTIONS : contains
    QUIZZES ||--o{ QUIZ_ATTEMPTS : attempted
    QUIZ_ATTEMPTS ||--o{ QUIZ_ANSWERS : records
    QUIZ_QUESTIONS ||--o{ QUIZ_ANSWERS : answered
    STUDY_PLANS ||--o{ STUDY_PLAN_ITEMS : contains
```

## Tablolar

| Tablo | Amaç ve önemli alanlar |
|---|---|
| `users` | Hesap, rol (`user`/`admin`), etkinlik, e-posta doğrulaması ve giriş sağlayıcısı. Etkin e-posta adresi küçük/büyük harf duyarsız unique index ile korunur. |
| `refresh_tokens` | Refresh token hash'i, aile kimliği, sona erme ve iptal zamanları. Ham token saklanmaz. |
| `email_verification_tokens`, `password_reset_tokens` | Tek kullanımlık kodların hash'i, süresi, kullanımı ve en fazla beş deneme sayısı. |
| `memberships` | `free`/`plus` üyelik ve `active`/`canceled`/`expired` durumu. Kullanıcı başına aynı anda en fazla bir aktif üyelik. |
| `categories` | Kullanıcının kategorileri; kullanıcı başına kategori adı harf duyarsız unique. Notu olan kategori silinemez. |
| `notes` | Yüklenen PDF/TXT dosyasının metadata'sı ve çıkarılmış metni. `index_status`: `pending`, `indexed` veya `failed`. |
| `summaries` | Kullanıcıya ve nota ait kartlar; `source`: `ai` veya `manual`, ayrıca sıra (`position`). |
| `conversations`, `messages` | Kategoriye bağlı sohbet ve `user`/`assistant` mesajları; mesaj kaynakları JSONB liste olarak saklanır. |
| `ai_jobs` | `index_note`, `summarize_note`, `chat_message`, `generate_quiz` işleri; durum, hata, model ve sonuç metadata'sı. |
| `quizzes`, `quiz_questions` | Kategori/opsiyonel not bağlantılı quiz, sıralı sorular, seçenekler, doğru seçenek indeksi ve açıklama. |
| `quiz_attempts`, `quiz_answers` | Kullanıcı denemesi, durum/skor ve soru başına seçilen yanıt. Deneme-soru çifti başına en fazla bir yanıt. |
| `study_plans`, `study_plan_items` | Pazartesi başlayan haftalık planlar ve gün/saat/konu bazlı bloklar. Kullanıcı başına hafta başlangıcı unique. |

## Silme davranışları ve kısıtlar

- Kullanıcı silindiğinde ona ait çoğu kayıt FK `ON DELETE CASCADE` ile temizlenir.
- Kategorinin notlarla ilişkisi `RESTRICT` olduğundan önce notlar kaldırılmalıdır.
- Not silinince özetler silinir; quiz, sohbet, AI işi ve plan öğesi gibi kaynak bağlantıları modelde tanımlandığı şekilde `SET NULL` veya `CASCADE` olur.
- Sohbet silinince mesajları silinir; quiz silinince soruları ve denemeleri silinir; plan silinince plan öğeleri silinir.
- Aktif bir not için aynı türden birden fazla `pending`/`running` AI işi, kısmi unique index ile engellenir. Sohbet başına da aynı anda tek aktif `chat_message` işi olabilir.
- Quiz seçenekleri ve mesaj `sources` alanı PostgreSQL'de JSONB'dir. UUID alanları PostgreSQL UUID olarak saklanır.

## Migration kullanımı

Model değişiklikleri doğrudan canlı veritabanına uygulanmamalıdır; Alembic migration oluşturup gözden geçirin ve uygulayın:

```sh
alembic current
alembic history
alembic upgrade head
```

Docker imajının başlangıç komutu `alembic upgrade head` çalıştırır. Uygulama başlangıcında `Base.metadata.create_all()` kullanılmaz.
