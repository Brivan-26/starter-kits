"""Forta agent scanning for batched transactions."""

from itertools import chain

from forta_agent import get_json_rpc_url
from forta_agent.transaction_event import TransactionEvent
from web3 import Web3

import src._balances as balances
import src._chains as chains
import src._events as events
import src._inputs as inputs
import src.metrics.airdrop as airdrop
import src.metrics.batch as batch
import src.metrics.erc20 as erc20
import src.metrics.native as native
import src.metrics.nft as nft
import src.options as options
import src.findings as findings

# INIT ########################################################################

CHAIN_ID = 1
WEB3 = Web3(Web3.HTTPProvider(get_json_rpc_url()))

def initialize():
    """Initialize the state variables that are tracked across tx and blocks."""
    global CHAIN_ID
    CHAIN_ID = int(WEB3.eth.chain_id)
    return {}

# METRICS #####################################################################

def _score_transaction(
    log: TransactionEvent,
    w3: Web3,
    min_transfer_count: int=options.MIN_TRANSFER_COUNT,
    min_transfer_total_erc20: int=options.MIN_TRANSFER_TOTAL_ERC20,
    min_transfer_total_native: int=options.MIN_TRANSFER_TOTAL_NATIVE,
    chain_id: int=1
) -> dict:
    """Estimate the probability that a transaction contains multiple transfers."""
    return {
        'batch': {
            'confidence': batch.confidence_score(log=log, w3=w3, min_transfer_count=min_transfer_count, min_transfer_total_erc20=min_transfer_total_erc20, min_transfer_total_native=min_transfer_total_native, max_batching_fee=options.MAX_BATCHING_FEE[chain_id]),
            'malicious': batch.malicious_score(log=log, w3=w3, min_transfer_count=min_transfer_count, max_batching_fee=options.MAX_BATCHING_FEE[chain_id])},
        'airdrop': {
            'confidence': airdrop.confidence_score(log=log, w3=w3, min_transfer_count=min_transfer_count, min_transfer_total=min_transfer_total_erc20),
            'malicious': airdrop.malicious_score(log=log, w3=w3)},
        'erc20': {
            'confidence': erc20.confidence_score(log=log, w3=w3, min_transfer_count=min_transfer_count, min_transfer_total=min_transfer_total_erc20),
            'malicious': erc20.malicious_score(log=log, w3=w3)},
        'erc721': {
            'confidence': nft.confidence_score(log=log, w3=w3, min_transfer_count=min_transfer_count),
            'malicious': nft.malicious_score(log=log, w3=w3)},
        'native': {
            'confidence': native.confidence_score(log=log, w3=w3, min_transfer_count=min_transfer_count, min_transfer_total=min_transfer_total_native, max_batching_fee=options.MAX_BATCHING_FEE[chain_id]),
            'malicious': native.malicious_score(log=log, w3=w3, min_transfer_count=min_transfer_count, max_batching_fee=options.MAX_BATCHING_FEE[chain_id])}}

# SCANNER #####################################################################

def handle_transaction_factory(
    w3: Web3,
    target_token: str=options.TARGET_TOKEN,
    target_contract: str=options.TARGET_CONTRACT,
    min_transfer_count: int=options.MIN_TRANSFER_COUNT,
    min_transfer_total_erc20: int=options.MIN_TRANSFER_TOTAL_ERC20,
    min_transfer_total_native: int=options.MIN_TRANSFER_TOTAL_NATIVE,
    min_confidence_score: float=options.MIN_CONFIDENCE_SCORE,
    min_malicious_score: int=options.MIN_MALICIOUS_SCORE
) -> callable:
    """Setup the main handler."""
    def _handle_transaction(log: TransactionEvent) -> list:
        """Main function called on the logs gathered by the Forta network."""
        global CHAIN_ID
        _findings = []
        _token = ''
        _transfers = []
        # parse the log
        _from = str(getattr(log.transaction, 'from_', '')).lower()
        _to = str(getattr(log.transaction, 'to', '')).lower()
        _data = str(getattr(log.transaction, 'data', '')).lower()
        _block = int(log.block.number)
        # filter by contract
        if target_contract in _to:
            # analyse the transaction
            _scores = _score_transaction(log=log, w3=w3, min_transfer_count=min_transfer_count, min_transfer_total_erc20=min_transfer_total_erc20, min_transfer_total_native=min_transfer_total_native, chain_id=CHAIN_ID)
            # identify the token
            if _scores['batch']['confidence'] >= min_confidence_score and _scores['batch']['malicious'] >= min_malicious_score:
                if _scores['erc20']['confidence'] >= 0.6:
                    _token = 'ERC20'
                    # filter by token
                    _transfers = [_e for _e in events.parse_log(log=log, abi=events.ERC20_TRANSFER_EVENT) if target_token in _e['token']]
                elif _scores['erc721']['confidence'] >= 0.6:
                    _token = 'ERC721'
                    # filter by token
                    _transfers = [_e for _e in events.parse_log(log=log, abi=events.ERC721_TRANSFER_EVENT) if target_token in _e['token']]
                elif _scores['native']['confidence'] >= 0.6:
                    _token = chains.CURRENCIES.get(CHAIN_ID, 'ETH')
                    _args = inputs.get_matching_arrays_of_address_and_value(data=_data, min_length=min_transfer_count)
                    _transfers = [{'token': '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', 'from': _to, 'to': _r, 'value': _v} for _a in _args for _r, _v in zip(*_a)]
                # raise an alert
                if _transfers:
                    _findings.append(findings.FormatBatchTxFinding(
                        sender=_from,
                        receiver=_to,
                        token=_token,
                        transfers=_transfers,
                        chain_id=CHAIN_ID,
                        confidence_score=_scores['batch']['confidence'],
                        malicious_score=_scores['batch']['malicious']))
        return _findings

    return _handle_transaction

# run with the default settings
handle_transaction = handle_transaction_factory(w3=WEB3)
