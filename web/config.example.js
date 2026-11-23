// 이 파일을 config.js 로 복사하고 값을 채우면 사이트의 '마감 알림 메일'이 실제로 동작해요.
// anon 키는 공개돼도 괜찮은 키예요(데이터베이스는 schema.sql의 함수로만 쓰기 가능).
// service_role 키는 절대 여기에 넣지 마세요.
window.GJ_CONFIG = {
  supabaseUrl: "https://YOUR-PROJECT.supabase.co",
  supabaseAnonKey: "YOUR-ANON-KEY"
};
