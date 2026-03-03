package com.mivzakim.news

import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlinx.coroutines.launch
import java.util.*

class NewsViewModel : ViewModel() {
    private val repo = NewsRepository()
    private var allItems by mutableStateOf<List<NewsItem>>(emptyList())
    var items by mutableStateOf<List<NewsItem>>(emptyList())
        private set
    var loading by mutableStateOf(false)
        private set
    private val seen = ArrayDeque<String>(100)
    private val spoken = ArrayDeque<String>(50)
    var newItem by mutableStateOf<NewsItem?>(null)
        private set
    var selectedSource by mutableStateOf<String?>(null)
        private set
    var sources by mutableStateOf<List<String>>(emptyList())
        private set

    fun markSpoken(title: String) {
        if (spoken.size >= 50) {
            spoken.removeFirst()
        }
        spoken.addLast(title)
    }

    fun hasBeenSpoken(title: String): Boolean = spoken.contains(title)

    init {
        startPolling()
    }

    fun setSource(source: String?) {
        selectedSource = source
        applyFilter()
    }

    private fun applyFilter() {
        items = if (selectedSource == null) {
            allItems
        } else {
            allItems.filter { it.source == selectedSource }
        }
    }

    private fun startPolling() {
        viewModelScope.launch {
            while (true) {
                load()
                kotlinx.coroutines.delay(60000)
            }
        }
    }

    private fun load() {
        loading = true
        viewModelScope.launch {
            val fetched = repo.fetch()
            if (fetched.isNotEmpty()) {
                allItems = fetched
                sources = fetched.map { it.source }.distinct().sorted()
                applyFilter()

                val latest = fetched.first()
                if (!seen.contains(latest.title)) {
                    if (seen.size >= 100) {
                        seen.removeFirst()
                    }
                    seen.addLast(latest.title)
                    if (selectedSource == null || latest.source == selectedSource) {
                        newItem = latest
                    }
                }
            }
            loading = false
        }
    }
}

class MainActivity : ComponentActivity() {
    private var tts: TextToSpeech? = null
    private val ttsReady = mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        tts = TextToSpeech(this) { status ->
            Log.d("TTS", "Init status: $status")
            if (status == TextToSpeech.SUCCESS) {
                val result = tts?.setLanguage(Locale("iw"))
                Log.d("TTS", "Language set result: $result")
                ttsReady.value = true
            }
        }

        setContent {
            MaterialTheme {
                NewsScreen(
                    onSpeak = { text -> speak(text) },
                    ttsReady = ttsReady.value
                )
            }
        }
    }

    private fun speak(text: String) {
        Log.d("TTS", "Speak called: $text")
        if (ttsReady.value) {
            tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, null)
        }
    }

    override fun onDestroy() {
        tts?.shutdown()
        super.onDestroy()
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NewsScreen(vm: NewsViewModel = viewModel(), onSpeak: (String) -> Unit = {}, ttsReady: Boolean = false) {
    LaunchedEffect(vm.newItem, ttsReady) {
        val item = vm.newItem
        if (item != null && ttsReady && !vm.hasBeenSpoken(item.title)) {
            Log.d("NewsScreen", "Auto-speaking new item: ${item.title}")
            vm.markSpoken(item.title)
            kotlinx.coroutines.delay(500)
            onSpeak(item.title)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFFEEEEEE))
    ) {
        if (vm.sources.isNotEmpty()) {
            LazyRow(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color(0xFFDDDDDD)),
                contentPadding = PaddingValues(horizontal = 8.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                item {
                    FilterChip(
                        selected = vm.selectedSource == null,
                        onClick = { vm.setSource(null) },
                        label = { Text("הכל") }
                    )
                }
                items(vm.sources) { source ->
                    FilterChip(
                        selected = vm.selectedSource == source,
                        onClick = { vm.setSource(source) },
                        label = { Text(source) }
                    )
                }
            }
        }

        if (vm.loading && vm.items.isEmpty()) {
            Box(
                modifier = Modifier.fillMaxSize()
            ) {
                CircularProgressIndicator()
            }
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                items(vm.items) { item ->
                    NewsItemView(item, onClick = { onSpeak(item.title) })
                }
            }
        }
    }
}

@Composable
fun NewsItemView(item: NewsItem, onClick: () -> Unit = {}) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
    ) {
        Column(
            modifier = Modifier.padding(16.dp)
        ) {
            Text(
                text = item.title,
                style = MaterialTheme.typography.bodyLarge,
                textAlign = TextAlign.Right,
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(modifier = Modifier.height(8.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    text = item.source,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.secondary
                )
                Text(
                    text = item.time,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.secondary
                )
            }
        }
    }
}
