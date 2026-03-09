from typing import Dict, Type

from app.parsers.base_parser import BaseParser


class ParserFactory:
    _parsers: Dict[str, Type[BaseParser]] = {}

    @classmethod
    def register(cls, bank_code: str, parser_class: Type[BaseParser]):
        cls._parsers[bank_code] = parser_class

    @classmethod
    def get_parser(cls, bank_code: str) -> BaseParser:
        from app.config import get_settings
        settings = get_settings()
        bank_config = settings.banks.get(bank_code, {})

        # Lazy import and register parsers
        if not cls._parsers:
            cls._register_all()

        parser_class = cls._parsers.get(bank_code)
        if not parser_class:
            raise ValueError(f"No parser registered for bank: {bank_code}")

        return parser_class(bank_config)

    @classmethod
    def _register_all(cls):
        from app.parsers.sinopac_parser import SinopacParser
        cls._parsers["sinopac"] = SinopacParser
        from app.parsers.taishin_parser import TaishinParser
        cls._parsers["taishin"] = TaishinParser
        from app.parsers.esun_parser import EsunParser
        cls._parsers["esun"] = EsunParser
        from app.parsers.fubon_parser import FubonParser
        cls._parsers["fubon"] = FubonParser
        # from app.parsers.cathay_parser import CathayParser
        # cls._parsers["cathay"] = CathayParser
        # from app.parsers.esun_parser import EsunParser
        # cls._parsers["esun"] = EsunParser
        # from app.parsers.rakuten_parser import RakutenParser
        # cls._parsers["rakuten"] = RakutenParser
        # from app.parsers.ctbc_parser import CtbcParser
        # cls._parsers["ctbc"] = CtbcParser
