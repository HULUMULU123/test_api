import asyncio
import json
from playwright.async_api import async_playwright

URL = (
    "https://nspd.gov.ru/map"
    "?thematic=PKK"
    "&zoom=5"
    "&coordinate_x=7804891.637510094"
    "&coordinate_y=8181287.398947453"
    "&baseLayerId=235"
    "&theme_id=1"
    "&is_copy_url=true"
    "&active_layers=36048"
)


async def parse_cadastral(cad_number: str):
    async with async_playwright() as p:
        # Chromium с аргументами для Linux/Xvfb/WebGL
        browser = await p.chromium.launch(
            headless=False,             # обязательно headful
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-dev-shm-usage",
                "--ignore-certificate-errors",
                "--disable-features=VizDisplayCompositor"
            ]
        )
        
        context = await browser.new_context(
            viewport={"width":1600,"height":900},
            ignore_https_errors=True,
            java_script_enabled=True
        )
        
        page = await context.new_page()

        await page.goto(URL, wait_until="networkidle", timeout=120000)

        # --- ждём появления поля поиска ---
        await page.wait_for_selector("m-input input", timeout=60000)
        input_handle = await page.evaluate_handle(
            """() => document.querySelector('m-input').shadowRoot.querySelector('input')"""
        )
        await input_handle.type(cad_number)
        await page.keyboard.press("Enter")

        # --- ждём suggestion и кликаем ---
        try:
            suggestion = page.locator("m-search-hints li").first
            await suggestion.wait_for(timeout=10000)
            await suggestion.click()
        except:
            pass

        # --- ждём рендера карты (canvas с пикселями) ---
        await page.wait_for_function("""
        () => {
            const canvas = document.querySelector('canvas.leaflet-layer');
            if (!canvas) return false;
            const ctx = canvas.getContext('2d');
            return ctx && ctx.getImageData(0,0,1,1).data[3] !== 0;
        }
        """, timeout=60000)

        # --- открыть аккордеон с объектами ---
        accordion = page.locator("div.accordion.open").first
        await accordion.wait_for(timeout=10000)

        # --- собираем объекты ---
        object_buttons = accordion.locator("button.accordion-item.clickable")
        num_objects = await object_buttons.count()
        all_objects_info = []

        for i in range(num_objects):
            button = object_buttons.nth(i)
            await button.scroll_into_view_if_needed()
            await button.click()
            await page.wait_for_timeout(2000)

            param_gen = page.locator("#attribute-slot m-parameter-generator")
            param_count = await param_gen.count()

            for j in range(param_count):
                gen = param_gen.nth(j)
                info_divs = gen.locator("div.info-container")
                info_count = await info_divs.count()

                parsed_info = []
                for k in range(info_count):
                    div = info_divs.nth(k)
                    header_elem = div.locator("m-typography").first
                    value_elem_string = div.locator("m-string-item").first
                    value_elem_typography = div.locator("m-typography").nth(1)

                    header_text = None
                    value_text = None

                    if await header_elem.count() > 0:
                        header_text = await header_elem.get_attribute("text")
                    if await value_elem_string.count() > 0:
                        value_text = await value_elem_string.get_attribute("text")
                    elif await value_elem_typography.count() > 0:
                        value_text = await value_elem_typography.get_attribute("text")

                    parsed_info.append({"header": header_text, "value": value_text})

                all_objects_info.append(parsed_info)

        # --- уменьшение масштаба карты ---
        zoom_out_button = page.locator(
            'm-tooltip[content="Уменьшить масштаб карты"] m-button'
        ).first
        for _ in range(3):
            await zoom_out_button.click()
            await page.wait_for_timeout(500)

        # --- скрин карты ---
        await page.wait_for_timeout(1500)
        screenshot_path = f"map_screenshot_{cad_number.replace(':','_')}.png"
        await page.screenshot(path=screenshot_path, full_page=True)

        result = {
            "cad_number": cad_number,
            "found": len(all_objects_info) > 0,
            "objects_info": all_objects_info,
            "screenshot": screenshot_path
        }

        await browser.close()
        return result


async def main():
    cad_number = "27:09:0000103:1627"
    result = await parse_cadastral(cad_number)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
