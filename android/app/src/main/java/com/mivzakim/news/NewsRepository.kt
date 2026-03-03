package com.mivzakim.news

import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.simpleframework.xml.core.Persister
import java.text.SimpleDateFormat
import java.util.*
import java.util.concurrent.TimeUnit

class NewsRepository {
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()
    private val xml = Persister()
    private val rfc = SimpleDateFormat("EEE, dd MMM yyyy HH:mm:ss Z", Locale.US)
    private val fmt = SimpleDateFormat("HH:mm", Locale.getDefault())

    suspend fun fetch(): List<NewsItem> = withContext(Dispatchers.IO) {
        try {
            Log.d("NewsRepo", "Fetching news...")
            val req = Request.Builder()
                .url("https://rss.mivzakim.net/rss/category/1")
                .build()

            val res = client.newCall(req).execute()
            Log.d("NewsRepo", "Response code: ${res.code}")
            val body = res.body?.string() ?: return@withContext emptyList()
            Log.d("NewsRepo", "Fetched ${body.length} bytes")

            val rss = xml.read(Rss::class.java, body)
            rss.channel?.items?.mapNotNull { item ->
                val t = item.title ?: return@mapNotNull null
                val p = item.pubDate ?: return@mapNotNull null
                val s = item.source?.split(" - ")?.firstOrNull() ?: "RSS"

                val time = try {
                    fmt.format(rfc.parse(p) ?: Date())
                } catch (e: Exception) {
                    p
                }

                NewsItem(t.trim(), time, s.trim())
            }?.take(10) ?: emptyList()
        } catch (e: Exception) {
            Log.e("NewsRepo", "Error fetching news", e)
            emptyList()
        }
    }
}
