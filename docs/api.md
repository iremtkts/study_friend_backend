# API rehberi

## Temel bilgiler

- API prefix'i: `/api/v1`
- OpenAPI UI: `/docs`; ReDoc: `/redoc`; şema: `/openapi.json`
- JSON isteklerinde `Content-Type: application/json` kullanın. Not yükleme multipart form-data'dır.
- Korumalı uçlar: `Authorization: Bearer <access_token>`.
- Başka kullanıcıya ait veya bulunmayan kaynaklar çoğunlukla aynı `404` yanıtını verir.
- Alan doğrulama hataları `422`; eksik/geçersiz kimlik bilgileri `401`; doğrulanmamış e-posta ile login `403`; limit ve çakışma durumlarında `429`/`409` döner.

## Uç kataloğu

### Sistem ve kimlik doğrulama

| Yöntem | Yol | Kimlik doğrulama | Açıklama |
|---|---|---:|---|
| `GET` | `/` | Hayır | Servis çalışıyor yanıtı. |
| `GET` | `/health` | Hayır | Container healthcheck ucu. |
| `POST` | `/api/v1/auth/register` | Hayır | Yerel hesap oluşturur ve e-posta doğrulama kodu yollar. `201`. |
| `POST` | `/api/v1/auth/login` | Hayır | Doğrulanmış hesap için access/refresh token çifti döndürür. |
| `POST` | `/api/v1/auth/refresh` | Hayır | Refresh token'ı döndürür (rotate) ve yeni token çifti üretir. |
| `POST` | `/api/v1/auth/logout` | Hayır | İstek gövdesindeki refresh token'ı iptal eder. |
| `POST` | `/api/v1/auth/logout-all` | Evet | Kullanıcının etkin refresh token'larını iptal eder. |
| `POST` | `/api/v1/auth/password/forgot` | Hayır | Genel yanıtla parola sıfırlama akışını başlatır. |
| `POST` | `/api/v1/auth/password/reset` | Hayır | E-posta, 6 haneli kod ve yeni parola ile sıfırlar. |
| `POST` | `/api/v1/auth/email/verify` | Hayır | E-posta ve 6 haneli doğrulama kodunu doğrular. |
| `POST` | `/api/v1/auth/email/resend-verification` | Hayır | Genel yanıtla doğrulama kodunu yeniden yollar. |
| `GET` | `/api/v1/auth/google` | Hayır | Google OAuth'a yönlendirir. |
| `GET` | `/api/v1/auth/google/callback` | Hayır | Google dönüşünü doğrular ve token çifti döndürür. |
| `DELETE` | `/api/v1/users/me` | Evet | Hesabı pasifleştirir ve refresh token'larını iptal eder. |

Yerel kayıt ve parola sıfırlama parolaları en az 12 karakter olmalıdır. OAuth callback için `code`, `state` query parametreleri ve state cookie'si gerekir.

### Kategoriler ve notlar

| Yöntem | Yol | Kimlik doğrulama | Açıklama |
|---|---|---:|---|
| `POST` | `/api/v1/categories` | Evet | `{ "name": "Fizik" }` ile kategori oluşturur. |
| `GET` | `/api/v1/categories` | Evet | Kullanıcının kategorilerini listeler. |
| `PATCH` | `/api/v1/categories/{category_id}` | Evet | Kategori adını günceller. |
| `DELETE` | `/api/v1/categories/{category_id}` | Evet | Not bağlı değilse siler; not varsa `409`. |
| `POST` | `/api/v1/notes` | Evet | Multipart: `category_id`, `file` (PDF/TXT), isteğe bağlı `title`. `201`; indeks işi arka planda kuyruğa alınabilir. |
| `GET` | `/api/v1/notes?category_id=...` | Evet | Kategori notlarını listeler. |
| `GET` | `/api/v1/notes/{note_id}` | Evet | Not detayını, `extracted_text` dahil, döndürür. |
| `DELETE` | `/api/v1/notes/{note_id}` | Evet | Notu ve saklanan dosyayı siler. |

### Özet kartları ve AI işleri

| Yöntem | Yol | Kimlik doğrulama | Açıklama |
|---|---|---:|---|
| `GET` | `/api/v1/summaries?category_id=...` | Evet | Kategori kartları; isteğe bağlı `note_id`, `limit`, `offset`. |
| `GET` | `/api/v1/notes/{note_id}/summaries` | Evet | Nota ait kartları sayfalar. |
| `POST` | `/api/v1/notes/{note_id}/summaries` | Evet | `{ "content": "..." }` ile manuel kart ekler. |
| `PATCH` | `/api/v1/summaries/{summary_id}` | Evet | Kart içeriğini günceller. |
| `DELETE` | `/api/v1/summaries/{summary_id}` | Evet | Kartı siler. |
| `POST` | `/api/v1/notes/{note_id}/summaries/generate` | Evet | AI özet işi başlatır; `202`. |
| `POST` | `/api/v1/notes/{note_id}/index` | Evet | Not indeksleme işi başlatır; `202`. |
| `POST` | `/api/v1/notes/{note_id}/quizzes/generate` | Evet | `question_count` ile AI quiz işi başlatır; `202`. Varsayılan 5, aralık 3–10. |
| `GET` | `/api/v1/ai-jobs/{job_id}` | Evet | İş durumunu ve sonuç metadata'sını döndürür. |

Özet/quiz/indeks işi başlatma yanıtı `job_id` ve `status` içerir. Chat ayrıca `message_id` döndürür. `GET /api/v1/ai-jobs/{job_id}` sorgulama yanıtı:

```json
{
  "job_id": "UUID",
  "conversation_id": "UUID-or-null",
  "result_id": "UUID-or-null",
  "status": "pending",
  "error_code": null,
  "result_count": null,
  "created_at": "ISO-8601",
  "started_at": null,
  "finished_at": null
}
```

Tam yanıt alanları `job_id`, `conversation_id`, `result_id`, `status`, `error_code`, `result_count`, `created_at`, `started_at` ve `finished_at` şeklindedir. `status` `pending`, `running`, `done` veya `failed` olabilir. Quiz üretimi tamamlandığında `result_id` oluşan quiz'in ID'sidir. `error_code` başarısız işlerde hata sınıfını bildirir; ham n8n yanıtı dönülmez. Kullanıcı ve plana göre günlük AI limitleri uygulanır.

Durum sorgusu, zaman aşımı eşiğini aşmış `pending`/`running` işleri `failed` (`error_code: "timeout"`) olarak işaretleyebilir.

### Sohbet

| Yöntem | Yol | Kimlik doğrulama | Açıklama |
|---|---|---:|---|
| `POST` | `/api/v1/conversations` | Evet | Kategori ve isteğe bağlı nota bağlı konuşma oluşturur. |
| `GET` | `/api/v1/conversations?category_id=...` | Evet | Konuşmaları sayfalar (`limit`, `offset`). |
| `GET` | `/api/v1/conversations/{conversation_id}` | Evet | Konuşma ve sayfalanmış mesajlar (`messages_limit`, `messages_offset`). |
| `PATCH` | `/api/v1/conversations/{conversation_id}` | Evet | Başlığı günceller. |
| `DELETE` | `/api/v1/conversations/{conversation_id}` | Evet | Konuşmayı ve mesajlarını siler. |
| `POST` | `/api/v1/conversations/{conversation_id}/chat` | Evet | Kullanıcı mesajı kaydeder ve AI yanıt işini başlatır; `202`. |

Chat isteği yalnızca `content` kabul eder; sunucu baştaki/sondaki boşlukları temizler ve 1–4000 karakter ister:

```json
{ "content": "Newton'un ikinci yasasını açıklar mısın?" }
```

`202` yanıtı:

```json
{
  "job_id": "UUID",
  "status": "pending",
  "message_id": "UUID"
}
```

Bir konuşmada aktif chat işi varsa `409`, günlük chat kotası doluysa `429`, n8n ayarı eksikse `503` döner. Asistan yanıtı tamamlanınca konuşma detayından okunabilir. Mesaj yanıtında `sources` string listesi olarak sunulur; kullanıcı mesajlarında boş olabilir. Eski `POST /api/v1/conversations/{id}/messages` route'u geriye uyumluluk için tutulur, fakat OpenAPI belgesinde gizlidir.

### Quiz ve denemeler

| Yöntem | Yol | Kimlik doğrulama | Açıklama |
|---|---|---:|---|
| `POST` | `/api/v1/quizzes` | Evet | Manuel quiz ve soruları oluşturur. |
| `GET` | `/api/v1/quizzes?category_id=...` | Evet | Kategori quizlerini listeler; isteğe bağlı `note_id`, `limit`, `offset`. |
| `GET` | `/api/v1/quizzes/{quiz_id}` | Evet | Quiz ve herkese açık soru alanlarını döndürür. |
| `DELETE` | `/api/v1/quizzes/{quiz_id}` | Evet | Quiz'i siler. |
| `POST` | `/api/v1/quizzes/{quiz_id}/attempts` | Evet | Deneme başlatır; mevcut açık deneme varsa onu döndürür. |
| `POST` | `/api/v1/attempts/{attempt_id}/submit` | Evet | Yanıtları gönderip denemeyi tamamlar. Boş bırakılan soru için `selected_index: null` gönderilebilir. |
| `GET` | `/api/v1/attempts/{attempt_id}` | Evet | Açık veya tamamlanmış denemeyi getirir. |
| `GET` | `/api/v1/attempts?quiz_id=...` | Evet | Tamamlanmış denemeleri listeler; `quiz_id` isteğe bağlıdır. |
| `GET` | `/api/v1/attempts/{attempt_id}/recommendation` | Evet | Yanlış/boş sorular ve deterministik çalışma planı önerir; DB'ye yazmaz. |
| `GET` | `/api/v1/stats?category_id=...` | Evet | Tamamlanmış quiz denemelerinin istatistikleri; kategori filtresi isteğe bağlı. |

Manuel quiz örneği:

```json
{
  "category_id": "UUID",
  "note_id": "UUID",
  "title": "Hareket ve Kuvvet",
  "questions": [
    {
      "question_text": "İvme hangi büyüklüğün zamana göre değişimidir?",
      "options": ["Hız", "Kütle", "Kuvvet"],
      "correct_index": 0,
      "explanation": "İvme, hızın zamana göre değişimidir."
    }
  ]
}
```

Quiz oluşturma doğrulamalarının üst sınırları `QUIZ_*` ayarlarıyla kontrol edilir. Quiz'in `correct_index` ve `explanation` alanları quiz görüntüleme yanıtında açıklanmaz; tamamlanmış deneme sonuçları ise bu alanları içerir.

### Çalışma planları

| Yöntem | Yol | Kimlik doğrulama | Açıklama |
|---|---|---:|---|
| `POST` | `/api/v1/study-plans` | Evet | Haftalık plan ve öğelerini oluşturur. |
| `GET` | `/api/v1/study-plans?week_start=...` | Evet | Planları listeler; hafta filtresi isteğe bağlı. |
| `GET` | `/api/v1/study-plans/{plan_id}` | Evet | Planı, öğeleri ve ilerlemeyi döndürür. |
| `DELETE` | `/api/v1/study-plans/{plan_id}` | Evet | Planı ve öğelerini siler. |
| `POST` | `/api/v1/study-plans/{plan_id}/items` | Evet | Var olan plana öğe ekler. |
| `PATCH` | `/api/v1/study-plan-items/{item_id}` | Evet | Plan öğesini veya tamamlanma durumunu günceller. |
| `DELETE` | `/api/v1/study-plan-items/{item_id}` | Evet | Plan öğesini siler. |

`week_start` pazartesi olmalıdır. Öğe alanları: `day_of_week` (1=pazartesi, 7=pazar), `start_time`, `duration_minutes`, `topic`, `category_id`, `note_id`. Öneriden gelen `week_start` ve `items` doğrudan plan oluşturma gövdesinde kullanılabilir:

```json
{
  "week_start": "2026-11-09",
  "title": "Quiz tekrarı",
  "items": [
    {
      "day_of_week": 5,
      "start_time": "18:00:00",
      "duration_minutes": 25,
      "topic": "Tekrar: İvme sorusu",
      "category_id": "UUID",
      "note_id": "UUID"
    }
  ]
}
```

## Temsilî yanıt şemaları

### Quiz soru görünürlüğü

`GET /api/v1/quizzes/{quiz_id}` içindeki soru nesnesi:

```json
{
  "id": "UUID",
  "position": 0,
  "question_text": "Soru metni",
  "options": ["A", "B", "C"],
  "created_at": "ISO-8601"
}
```

`correct_index` ve `explanation` quiz çözümü öncesi bu yanıtta yer almaz.

### Öneri yanıtı

`GET /api/v1/attempts/{attempt_id}/recommendation` yanıtı `summary`, `weak_topics`, `suggested_plan` ve `message` alanlarına sahiptir. Her `weak_topic` yanlış veya boş bırakılan sorudur; `related_summaries` eşleşme bulunmazsa boş listedir. `suggested_plan.items` alanları yukarıdaki çalışma planı öğesi şemasıyla uyumludur. Bu GET endpoint'i veritabanına kayıt yazmaz.
