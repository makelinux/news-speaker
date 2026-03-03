package com.mivzakim.news

import org.simpleframework.xml.Element
import org.simpleframework.xml.ElementList
import org.simpleframework.xml.Root

@Root(name = "item", strict = false)
data class Item(
    @field:Element(name = "title", required = false)
    var title: String? = null,

    @field:Element(name = "pubDate", required = false)
    var pubDate: String? = null,

    @field:Element(name = "source", required = false)
    var source: String? = null
)

@Root(name = "channel", strict = false)
data class Channel(
    @field:ElementList(name = "item", inline = true, required = false)
    var items: List<Item>? = null
)

@Root(name = "rss", strict = false)
data class Rss(
    @field:Element(name = "channel", required = false)
    var channel: Channel? = null
)

data class NewsItem(
    val title: String,
    val time: String,
    val source: String
)
