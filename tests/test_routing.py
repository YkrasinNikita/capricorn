"""Табличный тест маршрутизации: фраза → ожидаемый интент. Без Win32 и микрофона."""
import pytest

from actions.routes import ROUTES, match_route

CASES = [
    # поведение ассистента
    ("помаши ручкой", "wave"),
    ("погуляй", "roam"),
    ("что ты умеешь", "help"),
    ("переместись в левый нижний угол", "move"),
    ("спрячься", "hide"),
    # система
    ("громкость 50", "volume"),
    ("сделай громче", "volume"),
    ("выключи звук", "volume"),
    ("верни звук", "volume"),
    ("яркость 40", "brightness"),
    ("ярче", "brightness"),
    ("выключи компьютер", "power"),
    ("перезагрузи компьютер", "power"),
    ("заблокируй компьютер", "lock"),
    ("проверь компьютер", "analyze"),
    ("какой микрофон", "mic_info"),
    ("смени микрофон", "mic_next"),
    ("сделай скриншот", "screenshot"),
    # медиа
    ("поставь на паузу", "media_pause"),
    ("продолжи музыку", "media_play"),
    ("следующий трек", "media_next"),
    ("предыдущий трек", "media_prev"),
    ("что сейчас играет", "media_info"),
    ("перемотай на 30 секунд", "media_seek_absolute"),
    ("включи видео с 2 минут", "media_seek"),
    # клавиши и прокрутка
    ("нажми вин таб", "press_key"),
    ("напиши привет", "type_text"),
    ("прокрути вниз", "scroll"),
    ("листай медленно", "scroll"),
    ("поиск", "search_field"),
    ("закрой вкладку", "tab_close"),
    ("новая вкладка", "tab_nav"),
    ("сверни все окна", "minimize_all"),
    ("сверни окно", "minimize"),
    # программы
    ("открой ютуб", "open"),
    ("открой телеграм мама", "chat_person"),
    ("закрой хром", "close"),
    ("сними задачу стим", "kill_process"),
    ("что грузит компьютер", "top_processes"),
    ("создай папку проекты", "create"),
    # интернет
    ("найди в интернете погода", "search"),
    ("найди фото кота", "images"),
    ("расскажи информацию о москве", "info"),
    ("включи видео кот", "video"),
    # память и свои ответы
    ("запомни этот сайт как музыка", "remember_site"),
    ("забудь сайт музыка", "forget_site"),
    ("когда я скажу как дела отвечай лучше всех", "reply_set"),
    ("забудь ответ на как дела", "reply_forget"),
    # напоминания
    ("напомни через 10 минут позвонить маме", "reminder_add"),
    ("напомни в 18:30 о встрече", "reminder_add"),
    ("какие напоминания", "reminder_list"),
    ("отмени напоминание", "reminder_cancel"),
    # заметки
    ("запомни что кот спит на клавиатуре", "note_add"),
    ("что ты знаешь о встрече", "note_search"),
    ("покажи заметки", "note_list"),
    ("удали заметку про кота", "note_delete"),
    # поиск по документам (RAG)
    ("найди в документах бюджет проекта", "rag_ask"),
    ("что сказано в документах о бюджете", "rag_ask"),
    ("проиндексируй документы", "rag_index"),
    ("обнови индекс документов", "rag_index"),
    # время
    ("который час", "time"),
    ("какая сегодня дата", "date"),
]


@pytest.mark.parametrize("phrase, intent", CASES)
def test_route_table(phrase, intent):
    assert match_route(phrase) == intent


def test_route_table_is_big_enough():
    assert len(CASES) >= 30


def test_registry_names_and_order():
    """Все имена уникальны, обработчики описаны строками или callable, реестр не пустой."""
    names = [name for name, _, _ in ROUTES]
    assert len(names) == len(set(names))
    assert len(ROUTES) == 54
    for name, pattern, handler in ROUTES:
        assert isinstance(name, str) and name
        assert pattern
        assert callable(handler) or isinstance(handler, str)


def test_more_specific_route_wins():
    """Порядок реестра сохранён: «закрой вкладку» — вкладка, а не процесс."""
    assert match_route("закрой вкладку") == "tab_close"
    assert match_route("закрой хром") == "close"


def test_unknown_phrase_has_no_intent():
    assert match_route("нарисуй мне кота") is None


def test_actions_registry_is_wired(actions):
    """Actions получает тот же реестр и умеет находить интент без выполнения команды."""
    assert actions.routes is ROUTES
    actions.last_intent = None
    assert match_route("который час") == "time"
