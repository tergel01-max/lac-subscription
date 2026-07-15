# ChatGPT "gold" benchmark (4 sentences)

Reference outputs the user liked from the ChatGPT portal, used to benchmark our
system's output (project `TRP-00003`). Goal: our recommended (`ai_suggestion`)
should match this quality; our notes (`ai_rationale`) should surface the same
issues.

---

## SEQ 1
**EN:** The food industry follows the new needs by fortifying empty products with ingredients one normally finds in whole foods.

**Draft:** Хүнсний үйлдвэрлэл нь хоосон хүнсийг бүхэллэг хүнсэнд тааралддаг найрлагаар баяжуулах замаар энэхүү шинэ хэрэгцээг хангахыг зорьж байна.

**Issues ChatGPT flagged:** `хоосон хүнс` (calque) → `шим тэжээл багатай хүнс`; `тааралддаг` (colloquial) → `агуулагддаг`; `зорьж байна` adds meaning not in "follows".

**Gold (official book):** Хүнсний үйлдвэрлэл нь шинэ хэрэгцээнд нийцүүлэн, шим тэжээл багатай хүнсийг бүхэллэг хүнсэнд агуулагддаг найрлагаар баяжуулж байна.

---

## SEQ 2
**EN:** The free sale of vitamins, minerals, and other nutrients such as OPCs through channels not monopolized by the pharmaceutical industry significantly disturbs that industry's profits and profit potential.

**Draft:** Витамин, эрдэс бодис, түүнчлэн OPC зэрэг шим тэжээлийг эмийн үйлдвэрлэлийн монополь хяналтаас гадуур чөлөөтэй худалдаалах нь тус салбарын ашиг орлого, ирээдүйн ашгийн боломжид ноцтойгоор нөлөөлдөг.

**Issues:** `монополь хяналтаас гадуур` too literal/strong (means channels outside the industry's exclusive control); `ашгийн боломж` awkward → `ашиг олох боломж`; "disturbs" = disrupts/challenges, not necessarily "damages" (avoid overstating).

**Gold (recommended):** Витамин, эрдэс бодис болон OPC зэрэг бусад шим тэжээлт бодисууд эмийн салбарын монополь хяналтаас гадуур чөлөөтэй худалдаалагдах нь тус салбарын одоогийн ашиг орлого болон ирээдүйн ашиг олох боломжид ихээхэн нөлөөлдөг.

**Gold (natural book style):** Витамин, эрдэс бодис, OPC зэрэг шим тэжээлүүд эмийн салбарын дангаар хянадаг худалдааны сувгаас гадуур чөлөөтэй хүртээмжтэй болох нь тухайн салбарын ашиг орлого болон цаашдын өсөлтийн боломжид ихээхэн нөлөөлж байна.

---

## SEQ 3
**EN:** Although proud of his work, Masquelier never sought to place his discoveries and inventions in a scope broader than the framework of his own day-to-day "magistral" work at the university.

**Draft:** Маскэлье бүтээлээрээ бахархдаг байсан ч нээлтүүдээ их сургуульдаа "ур чадвараа шингэн" хийдэг ажлынхаа хүрээнээс гаргаж, илүү өргөн цар хүрээнд тавьж үзэхийг хэзээ ч эрмэлзээгүй.

**Issues:** `"ур чадвараа шингэн"` is a wrong/typo rendering of "magistral" (his everyday professional/academic work); `тавьж үзэх` colloquial; readers won't understand transliterated `магистрал`.

**Gold (natural book style):** Маскэлье өөрийн бүтээлээрээ бахархдаг байсан ч их сургуульд хийдэг мэргэжлийн ажлынхаа хүрээнээс хальж, нээлтүүдээ илүү өргөн хүрээнд түгээн дэлгэрүүлэхийг хэзээ ч зорьж байгаагүй.

---

## SEQ 4
**EN:** He never tried to lift himself to Nobel Prize heights by stressing his research's implications for millions of people. Yet, the implications of his work are so profound that one cannot simply sum up the many varied health aspects of the products he developed just to serve the reader who is looking for a miracle cure.

**Draft:** Судалгааныхаа үр дүнг олон сая хүнд нөлөөлөх ач холбогдолтой хэмээн онцолж, Нобелийн шагналын түвшинд хүргэхийг ч зорьсонгүй. Гэсэн ч түүний ажлын үр нөлөө асар гүнзгий тул түүний бүтээсэн бүтээгдэхүүний эрүүл мэндэд үзүүлэх олон талт ач холбогдлыг зөвхөн "гайхамшигт эм" хайж буй уншигчийн хэрэгцээнд л нийцүүлэх зорилгоор товчхон дүгнэх боломжгүй юм.

**Issues:** "lift himself to Nobel Prize heights" is a metaphor for self-promotion/seeking fame, NOT literally reaching Nobel level; `implications` → `ач холбогдол`.

**Gold (literary book style):** Тэрээр судалгааныхаа ач холбогдлыг олон сая хүний амьдралд үзүүлэх нөлөөгөөр нь онцолж, өөрийн нэр хүндийг Нобелийн шагналын өндөрлөгт хүргэхийг хэзээ ч эрмэлзээгүй. Гэвч түүний бүтээлийн ач холбогдол үнэхээр өргөн хүрээтэй тул түүний хөгжүүлсэн бүтээгдэхүүний эрүүл мэндийн олон талт үнэ цэнийг зөвхөн гайхамшигт эмчилгээ эрэлхийлдэг уншигчдад зориулан энгийнээр хураангуйлах боломжгүй юм.
