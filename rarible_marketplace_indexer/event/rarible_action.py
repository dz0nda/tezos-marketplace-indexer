import uuid
from typing import Dict
from typing import Optional
from uuid import uuid5

from base58 import b58encode_check
from dipdup.datasources.tzkt.datasource import TzktDatasource
from dipdup.models import Transaction
from pytezos.michelson.parse import michelson_to_micheline
from pytezos.michelson.types import MichelsonType

from rarible_marketplace_indexer.event.abstract_action import AbstractAcceptBidEvent
from rarible_marketplace_indexer.event.abstract_action import AbstractAcceptFloorBidEvent
from rarible_marketplace_indexer.event.abstract_action import AbstractBidCancelEvent
from rarible_marketplace_indexer.event.abstract_action import AbstractFloorBidCancelEvent
from rarible_marketplace_indexer.event.abstract_action import AbstractLegacyOrderCancelEvent
from rarible_marketplace_indexer.event.abstract_action import AbstractLegacyOrderMatchEvent
from rarible_marketplace_indexer.event.abstract_action import AbstractOrderCancelEvent
from rarible_marketplace_indexer.event.abstract_action import AbstractOrderListEvent
from rarible_marketplace_indexer.event.abstract_action import AbstractOrderMatchEvent
from rarible_marketplace_indexer.event.abstract_action import AbstractPutBidEvent
from rarible_marketplace_indexer.event.dto import CancelDto
from rarible_marketplace_indexer.event.dto import LegacyMatchDto
from rarible_marketplace_indexer.event.dto import ListDto
from rarible_marketplace_indexer.event.dto import MakeDto
from rarible_marketplace_indexer.event.dto import MatchDto
from rarible_marketplace_indexer.event.dto import TakeDto
from rarible_marketplace_indexer.models import PlatformEnum
from rarible_marketplace_indexer.models import TransactionTypeEnum
from rarible_marketplace_indexer.types.rarible_api_objects.asset.enum import AssetClassEnum
from rarible_marketplace_indexer.types.rarible_bids.parameter.accept_bid import AcceptBidParameter
from rarible_marketplace_indexer.types.rarible_bids.parameter.accept_floor_bid import AcceptFloorBidParameter
from rarible_marketplace_indexer.types.rarible_bids.parameter.cancel_bid import CancelBidParameter
from rarible_marketplace_indexer.types.rarible_bids.parameter.cancel_floor_bid import CancelFloorBidParameter
from rarible_marketplace_indexer.types.rarible_bids.parameter.put_bid import PutBidParameter
from rarible_marketplace_indexer.types.rarible_bids.parameter.put_floor_bid import PutFloorBidParameter
from rarible_marketplace_indexer.types.rarible_bids.storage import RaribleBidsStorage
from rarible_marketplace_indexer.types.rarible_exchange.parameter.buy import BuyParameter
from rarible_marketplace_indexer.types.rarible_exchange.parameter.cancel_sale import CancelSaleParameter
from rarible_marketplace_indexer.types.rarible_exchange.parameter.sell import SellParameter
from rarible_marketplace_indexer.types.rarible_exchange.storage import RaribleExchangeStorage
from rarible_marketplace_indexer.types.rarible_exchange_legacy.parameter.match_orders import FA2AssetClass
from rarible_marketplace_indexer.types.rarible_exchange_legacy.parameter.match_orders import FA12AssetClass
from rarible_marketplace_indexer.types.rarible_exchange_legacy.parameter.match_orders import MatchOrdersParameter
from rarible_marketplace_indexer.types.rarible_exchange_legacy.parameter.match_orders import XTZAssetClass
from rarible_marketplace_indexer.types.rarible_exchange_legacy.storage import RaribleExchangeLegacyStorage
from rarible_marketplace_indexer.types.rarible_exchange_legacy_data.parameter.remove import RemoveParameter
from rarible_marketplace_indexer.types.tezos_objects.asset_value.asset_value import AssetValue
from rarible_marketplace_indexer.types.tezos_objects.asset_value.xtz_value import Xtz
from rarible_marketplace_indexer.types.tezos_objects.tezos_object_hash import ImplicitAccountAddress
from rarible_marketplace_indexer.types.tezos_objects.tezos_object_hash import OriginatedAccountAddress


class RaribleAware:
    unpack_map_take: Dict[int, str] = {
        0: '_take_xtz',
        1: '_take_fa12',
        2: '_take_fa2',
    }

    unpack_map_make: Dict[int, str] = {
        0: '_make_xtz',
        1: '_make_fa12',
        2: '_make_fa2',
    }

    @classmethod
    def _get_contract(cls, asset_bytes: bytes, offset: int) -> OriginatedAccountAddress:
        header = bytes.fromhex('025a79')
        return OriginatedAccountAddress(b58encode_check(header + asset_bytes[offset : offset + 20]).decode())

    @classmethod
    def _get_token_id(cls, asset_bytes: bytes) -> int:
        data, token_id, length = asset_bytes[31:], 0, 1

        while data[length - 1] & 0b10000000 != 0:
            length += 1

        for i in range(length - 1, 0, -1):
            token_id <<= 7
            token_id |= data[i] & 0b01111111

        token_id <<= 6
        token_id |= data[0] & 0b00111111

        if (data[0] & 0b01000000) != 0:
            token_id = -token_id

        return token_id

    @classmethod
    def _take_xtz(cls, value: int, asset_bytes: Optional[bytes] = None) -> TakeDto:
        assert not asset_bytes

        return TakeDto(asset_class=AssetClassEnum.XTZ, contract=None, token_id=None, value=Xtz.from_u_tezos(value))

    @classmethod
    def _take_fa12(cls, value: int, asset_bytes: bytes) -> TakeDto:
        return TakeDto(
            asset_class=AssetClassEnum.FUNGIBLE_TOKEN,
            contract=cls._get_contract(asset_bytes, 7),
            token_id=0,
            value=AssetValue(value),
        )

    @classmethod
    def _take_fa2(cls, value: int, asset_bytes: bytes) -> TakeDto:
        return TakeDto(
            asset_class=AssetClassEnum.FUNGIBLE_TOKEN,
            contract=cls._get_contract(asset_bytes, 9),
            token_id=cls._get_token_id(asset_bytes),
            value=AssetValue(value),
        )

    @classmethod
    def _make_xtz(cls, value: int, asset_bytes: Optional[bytes] = None) -> TakeDto:
        assert not asset_bytes

        return MakeDto(asset_class=AssetClassEnum.XTZ, contract=None, token_id=None, value=Xtz.from_u_tezos(value))

    @classmethod
    def _make_fa12(cls, value: int, asset_bytes: bytes) -> TakeDto:
        return MakeDto(
            asset_class=AssetClassEnum.FUNGIBLE_TOKEN,
            contract=cls._get_contract(asset_bytes, 7),
            token_id=0,
            value=AssetValue(value),
        )

    @classmethod
    def _make_fa2(cls, value: int, asset_bytes: bytes) -> TakeDto:
        return MakeDto(
            asset_class=AssetClassEnum.FUNGIBLE_TOKEN,
            contract=cls._get_contract(asset_bytes, 9),
            token_id=cls._get_token_id(asset_bytes),
            value=AssetValue(value),
        )

    @staticmethod
    def get_order_hash(
        contract: OriginatedAccountAddress,
        token_id: int,
        seller: ImplicitAccountAddress,
        platform: PlatformEnum,
        asset_class: str = None,
        asset: str = None,
    ) -> str:
        return uuid5(
            namespace=uuid.NAMESPACE_OID, name=f'{platform}/{TransactionTypeEnum.SALE}-{contract}:{token_id}@{seller}/{asset_class}-{asset}'
        ).hex

    @staticmethod
    def get_bid_hash(
        contract: OriginatedAccountAddress,
        token_id: int,
        bidder: ImplicitAccountAddress,
        platform: PlatformEnum,
        asset_class: str = None,
        asset: str = None,
    ) -> str:
        return uuid5(
            namespace=uuid.NAMESPACE_OID, name=f'{platform}/{TransactionTypeEnum.BID}-{contract}:{token_id}@{bidder}/{asset_class}-{asset}'
        ).hex

    @staticmethod
    def get_floor_bid_hash(
        contract: OriginatedAccountAddress,
        bidder: ImplicitAccountAddress,
        platform: PlatformEnum,
        asset_class: str = None,
        asset: str = None,
    ) -> str:
        return uuid5(
            namespace=uuid.NAMESPACE_OID, name=f'{platform}/{TransactionTypeEnum.FLOOR_BID}-{contract}@{bidder}/{asset_class}-{asset}'
        ).hex

    @classmethod
    def get_take_dto(cls, sale_type: int, value: int, asset_bytes: Optional[bytes] = None) -> TakeDto:
        method_name = cls.unpack_map_take.get(sale_type)
        take_method = getattr(cls, method_name)
        return take_method(value, asset_bytes)

    @classmethod
    def get_make_dto(cls, sale_type: int, value: int, asset_bytes: Optional[bytes] = None) -> MakeDto:
        method_name = cls.unpack_map_make.get(sale_type)
        make_method = getattr(cls, method_name)
        return make_method(value, asset_bytes)


class RaribleOrderListEvent(AbstractOrderListEvent):
    platform = PlatformEnum.RARIBLE_V2
    RaribleListTransaction = Transaction[SellParameter, RaribleExchangeStorage]

    @staticmethod
    def _get_list_dto(
        transaction: RaribleListTransaction,
        datasource: TzktDatasource,
    ) -> ListDto:
        internal_order_id = RaribleAware.get_order_hash(
            contract=OriginatedAccountAddress(transaction.parameter.s_asset_contract),
            token_id=int(transaction.parameter.s_asset_token_id),
            seller=ImplicitAccountAddress(transaction.data.sender_address),
            platform=RaribleOrderListEvent.platform,
            asset_class=transaction.parameter.s_sale_type,
            asset=transaction.parameter.s_sale_asset,
        )

        take = RaribleAware.get_take_dto(
            sale_type=int(transaction.parameter.s_sale_type),
            value=int(transaction.parameter.s_sale.sale_amount),
            asset_bytes=bytes.fromhex(transaction.parameter.s_sale_asset),
        )

        return ListDto(
            internal_order_id=internal_order_id,
            maker=ImplicitAccountAddress(transaction.data.sender_address),
            make=MakeDto(
                asset_class=AssetClassEnum.MULTI_TOKEN,
                contract=OriginatedAccountAddress(transaction.parameter.s_asset_contract),
                token_id=int(transaction.parameter.s_asset_token_id),
                value=AssetValue(transaction.parameter.s_sale.sale_asset_qty),
            ),
            take=take,
            start_at=transaction.parameter.s_sale.sale_start,
            end_at=transaction.parameter.s_sale.sale_end,
            origin_fees=transaction.parameter.s_sale.sale_origin_fees,
            payouts=transaction.parameter.s_sale.sale_payouts,
        )


class RaribleOrderCancelEvent(AbstractOrderCancelEvent):
    platform = PlatformEnum.RARIBLE_V2
    RaribleCancelTransaction = Transaction[CancelSaleParameter, RaribleExchangeStorage]

    @staticmethod
    def _get_cancel_dto(transaction: RaribleCancelTransaction, datasource: TzktDatasource) -> CancelDto:
        internal_order_id = RaribleAware.get_order_hash(
            contract=OriginatedAccountAddress(transaction.parameter.cs_asset_contract),
            token_id=int(transaction.parameter.cs_asset_token_id),
            seller=ImplicitAccountAddress(transaction.data.sender_address),
            platform=RaribleOrderCancelEvent.platform,
            asset_class=transaction.parameter.cs_sale_type,
            asset=transaction.parameter.cs_sale_asset,
        )

        return CancelDto(internal_order_id=internal_order_id)


class RaribleLegacyOrderCancelEvent(AbstractLegacyOrderCancelEvent):
    platform = PlatformEnum.RARIBLE_V1
    RaribleCancelTransaction = Transaction[RemoveParameter, RaribleExchangeLegacyStorage]

    @staticmethod
    def _get_legacy_cancel_dto(transaction: RaribleCancelTransaction, datasource: TzktDatasource) -> CancelDto:
        return CancelDto(internal_order_id=transaction.parameter.__root__)


class RaribleOrderMatchEvent(AbstractOrderMatchEvent):
    platform = PlatformEnum.RARIBLE_V2
    RaribleMatchTransaction = Transaction[BuyParameter, RaribleExchangeStorage]

    @staticmethod
    def _get_match_dto(transaction: RaribleMatchTransaction, datasource: TzktDatasource) -> MatchDto:
        internal_order_id = RaribleAware.get_order_hash(
            contract=OriginatedAccountAddress(transaction.parameter.b_asset_contract),
            token_id=int(transaction.parameter.b_asset_token_id),
            seller=ImplicitAccountAddress(transaction.parameter.b_seller),
            platform=RaribleOrderMatchEvent.platform,
            asset_class=transaction.parameter.b_sale_type,
            asset=transaction.parameter.b_sale_asset,
        )

        return MatchDto(
            internal_order_id=internal_order_id,
            match_amount=AssetValue(transaction.parameter.b_amount),
            match_timestamp=transaction.data.timestamp,
            taker=ImplicitAccountAddress(transaction.data.sender_address),
            token_id=None,
        )


class RaribleLegacyOrderMatchEvent(AbstractLegacyOrderMatchEvent):
    platform = PlatformEnum.RARIBLE_V1
    RaribleLegacyMatchTransaction = Transaction[MatchOrdersParameter, RaribleExchangeLegacyStorage]

    @staticmethod
    def _get_legacy_match_dto(transaction: RaribleLegacyMatchTransaction, datasource: TzktDatasource) -> LegacyMatchDto:
        make = RaribleAware.get_make_dto(
            sale_type=2,
            value=int(transaction.parameter.order_left.make_asset.asset_value),
            asset_bytes=bytes.fromhex(transaction.parameter.order_left.make_asset.asset_type.asset_data),
        )

        take_type = (
            0
            if transaction.parameter.order_left.take_asset.asset_type.asset_class is XTZAssetClass
            else 1
            if transaction.parameter.order_left.take_asset.asset_type.asset_class is FA12AssetClass
            else 2
            if transaction.parameter.order_left.take_asset.asset_type.asset_class is FA2AssetClass
            else 0
        )

        take_data = None if take_type == 0 else bytes.fromhex(transaction.parameter.order_left.make_asset.asset_type.asset_data)

        take = RaribleAware.get_take_dto(
            sale_type=take_type,
            value=int(transaction.parameter.order_left.take_asset.asset_value),
            asset_bytes=take_data,
        )

        internal_order_id = RaribleAware.get_order_hash(
            contract=OriginatedAccountAddress(make.contract),
            token_id=int(make.token_id),
            seller=ImplicitAccountAddress(transaction.parameter.order_left.maker),
            platform=RaribleLegacyOrderMatchEvent.platform,
            asset_class=take.asset_class,
            asset=bytes.fromhex(transaction.parameter.order_left.take_asset.asset_type.asset_data),
        )

        order_start = transaction.parameter.order_left.start
        start = transaction.parameter.order_left.start if order_start is not None else transaction.data.timestamp

        fees_type = MichelsonType.match(
            michelson_to_micheline(
                '(pair (list (pair (address %part_account) (nat %part_value))) (list (pair (address %part_account) (nat %part_value))))'
            )
        )
        fees = fees_type.unpack(bytes.fromhex(transaction.parameter.order_right.data)).to_python_object()

        return LegacyMatchDto(
            internal_order_id=internal_order_id,
            maker=ImplicitAccountAddress(transaction.data.sender_address),
            make=make,
            take=take,
            start=start,
            end_at=transaction.parameter.order_left.end,
            match_amount=AssetValue(int(transaction.parameter.order_left.make_asset.asset_value)),
            match_timestamp=transaction.data.timestamp,
            taker=ImplicitAccountAddress(transaction.data.sender_address),
            token_id=int(make.token_id),
            origin_fees=fees[1],
            payouts=fees[0],
        )


class RariblePutBidEvent(AbstractPutBidEvent):
    platform = PlatformEnum.RARIBLE_V2
    RariblePutBidTransaction = Transaction[PutBidParameter, RaribleBidsStorage]

    @staticmethod
    def _get_bid_dto(
        transaction: RariblePutBidTransaction,
        datasource: TzktDatasource,
    ) -> ListDto:
        internal_order_id = RaribleAware.get_bid_hash(
            contract=OriginatedAccountAddress(transaction.parameter.pb_asset_contract),
            bidder=ImplicitAccountAddress(transaction.data.sender_address),
            token_id=int(transaction.parameter.pb_asset_token_id),
            platform=RariblePutBidEvent.platform,
            asset_class=transaction.parameter.pb_bid_type,
            asset=transaction.parameter.pb_bid_asset,
        )

        make = RaribleAware.get_make_dto(
            sale_type=int(transaction.parameter.pb_bid_type),
            value=int(transaction.parameter.pb_bid.bid_amount),
            asset_bytes=bytes.fromhex(transaction.parameter.pb_bid_asset),
        )

        take = TakeDto(
            asset_class=AssetClassEnum.MULTI_TOKEN,
            contract=OriginatedAccountAddress(transaction.parameter.pb_asset_contract),
            token_id=int(transaction.parameter.pb_asset_token_id),
            value=AssetValue(transaction.parameter.pb_bid.bid_asset_qty),
        )

        return ListDto(
            internal_order_id=internal_order_id,
            maker=ImplicitAccountAddress(transaction.data.sender_address),
            make=make,
            take=take,
            start_at=transaction.data.timestamp,
            end_at=transaction.parameter.pb_bid.bid_expiry_date,
            origin_fees=transaction.parameter.pb_bid.bid_origin_fees,
            payouts=transaction.parameter.pb_bid.bid_payouts,
        )


class RariblePutFloorBidEvent(AbstractPutBidEvent):
    platform = PlatformEnum.RARIBLE_V2
    RariblePutFloorBidTransaction = Transaction[PutFloorBidParameter, RaribleBidsStorage]

    @staticmethod
    def _get_bid_dto(
        transaction: RariblePutFloorBidTransaction,
        datasource: TzktDatasource,
    ) -> ListDto:
        internal_order_id = RaribleAware.get_floor_bid_hash(
            contract=OriginatedAccountAddress(transaction.parameter.pfb_asset_contract),
            bidder=ImplicitAccountAddress(transaction.data.sender_address),
            platform=RariblePutFloorBidEvent.platform,
            asset_class=transaction.parameter.pfb_bid_type,
            asset=transaction.parameter.pfb_bid_asset,
        )

        make = RaribleAware.get_make_dto(
            sale_type=int(transaction.parameter.pfb_bid_type),
            value=int(transaction.parameter.pfb_bid.bid_amount),
            asset_bytes=bytes.fromhex(transaction.parameter.pfb_bid_asset),
        )

        take = TakeDto(
            asset_class=AssetClassEnum.COLLECTION,
            contract=OriginatedAccountAddress(transaction.parameter.pfb_asset_contract),
            token_id=None,
            value=AssetValue(transaction.parameter.pfb_bid.bid_asset_qty),
        )

        return ListDto(
            internal_order_id=internal_order_id,
            maker=ImplicitAccountAddress(transaction.data.sender_address),
            make=make,
            take=take,
            start_at=transaction.data.timestamp,
            end_at=transaction.parameter.pfb_bid.bid_expiry_date,
            origin_fees=transaction.parameter.pfb_bid.bid_origin_fees,
            payouts=transaction.parameter.pfb_bid.bid_payouts,
        )


class RaribleAcceptBidEvent(AbstractAcceptBidEvent):
    platform = PlatformEnum.RARIBLE_V2
    RaribleAcceptBidTransaction = Transaction[AcceptBidParameter, RaribleBidsStorage]

    @staticmethod
    def _get_accept_bid_dto(transaction: RaribleAcceptBidTransaction, datasource: TzktDatasource) -> MatchDto:
        internal_order_id = RaribleAware.get_bid_hash(
            contract=OriginatedAccountAddress(transaction.parameter.ab_asset_contract),
            token_id=int(transaction.parameter.ab_asset_token_id),
            bidder=ImplicitAccountAddress(transaction.parameter.ab_bidder),
            platform=RaribleAcceptBidEvent.platform,
            asset_class=transaction.parameter.ab_bid_type,
            asset=transaction.parameter.ab_bid_asset,
        )

        return MatchDto(
            internal_order_id=internal_order_id,
            match_timestamp=transaction.data.timestamp,
            taker=ImplicitAccountAddress(transaction.data.sender_address),
            token_id=int(transaction.parameter.ab_asset_token_id),
            match_amount=None,
        )


class RaribleAcceptFloorBidEvent(AbstractAcceptFloorBidEvent):
    platform = PlatformEnum.RARIBLE_V2
    RaribleAcceptFloorBidTransaction = Transaction[AcceptFloorBidParameter, RaribleBidsStorage]

    @staticmethod
    def _get_accept_floor_bid_dto(transaction: RaribleAcceptFloorBidTransaction, datasource: TzktDatasource) -> MatchDto:
        internal_order_id = RaribleAware.get_floor_bid_hash(
            contract=OriginatedAccountAddress(transaction.parameter.afb_asset_contract),
            bidder=ImplicitAccountAddress(transaction.parameter.afb_bidder),
            platform=RaribleAcceptFloorBidEvent.platform,
            asset_class=transaction.parameter.afb_bid_type,
            asset=transaction.parameter.afb_bid_asset,
        )

        return MatchDto(
            internal_order_id=internal_order_id,
            match_timestamp=transaction.data.timestamp,
            taker=ImplicitAccountAddress(transaction.data.sender_address),
            token_id=int(transaction.parameter.afb_asset_token_id),
            match_amount=None,
        )


class RaribleBidCancelEvent(AbstractBidCancelEvent):
    platform = PlatformEnum.RARIBLE_V2
    RaribleCancelBidTransaction = Transaction[CancelBidParameter, RaribleBidsStorage]

    @staticmethod
    def _get_cancel_bid_dto(transaction: RaribleCancelBidTransaction, datasource: TzktDatasource) -> CancelDto:
        internal_order_id = RaribleAware.get_bid_hash(
            contract=OriginatedAccountAddress(transaction.parameter.cb_asset_contract),
            token_id=int(transaction.parameter.cb_asset_token_id),
            bidder=ImplicitAccountAddress(transaction.data.sender_address),
            platform=RaribleBidCancelEvent.platform,
            asset_class=transaction.parameter.cb_bid_type,
            asset=transaction.parameter.cb_bid_asset,
        )

        return CancelDto(internal_order_id=internal_order_id)


class RaribleFloorBidCancelEvent(AbstractFloorBidCancelEvent):
    platform = PlatformEnum.RARIBLE_V2
    RaribleCancelFloorBidTransaction = Transaction[CancelFloorBidParameter, RaribleBidsStorage]

    @staticmethod
    def _get_cancel_floor_bid_dto(transaction: RaribleCancelFloorBidTransaction, datasource: TzktDatasource) -> CancelDto:
        internal_order_id = RaribleAware.get_floor_bid_hash(
            contract=OriginatedAccountAddress(transaction.parameter.cfb_asset_contract),
            bidder=ImplicitAccountAddress(transaction.data.sender_address),
            platform=RaribleFloorBidCancelEvent.platform,
            asset_class=transaction.parameter.cfb_bid_type,
            asset=transaction.parameter.cfb_bid_asset,
        )

        return CancelDto(internal_order_id=internal_order_id)
