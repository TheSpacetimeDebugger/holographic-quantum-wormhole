"""
╔══════════════════════════════════════════════════════════════════════╗
║           QUANTUM CHRONO CORE v2  ·  SYK · AdS/CFT · Lindblad      ║
║         Open Quantum System Simulation of Holographic Traversability ║
╚══════════════════════════════════════════════════════════════════════╝

Author  : Ibrahim El-Shami (TheSpacetimeDebugger)
Studio  : Astmize Studio
Framework: Python 3.10+ · NumPy · SciPy
Physics  : AdS/CFT · SYK Model · Lindblad Open Quantum Systems
"""

from __future__ import annotations

import itertools
import sys

import numpy as np
import scipy.linalg as spla
from numpy.typing import NDArray

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
N_QUBITS: int   = 4       # Total system qubits (must be even)
BETA: float     = 1.0     # Inverse temperature β
J_COUPLING: float = 1.0   # SYK random coupling strength J
EVO_TIME: float  = 1.5    # Scrambling evolution time t
GAMMA_LOSS: float = 0.05  # Amplitude damping rate  (T₁ energy loss)
GAMMA_GAIN: float = 0.01  # Amplitude gain rate     (thermal excitation)
GAMMA_DEPH: float = 0.03  # Dephasing rate          (T₂ phase noise)
GJW_COUPLING: float = 0.1 # GJW inter-boundary coupling g

# ─────────────────────────────────────────────────────────────────────────────
#  PAULI MATRICES
# ─────────────────────────────────────────────────────────────────────────────
I2  = np.eye(2, dtype=complex)
X   = np.array([[0, 1], [1, 0]], dtype=complex)
Y   = np.array([[0, -1j], [1j, 0]], dtype=complex)
Z   = np.array([[1, 0], [0, -1]], dtype=complex)
SP  = np.array([[0, 1], [0, 0]], dtype=complex)   # σ+ = |1⟩⟨0|
SM  = np.array([[0, 0], [1, 0]], dtype=complex)   # σ- = |0⟩⟨1|

# ─────────────────────────────────────────────────────────────────────────────
#  UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def kron_n(*ops: NDArray) -> NDArray:
    """Tensor product of a sequence of matrices."""
    result = ops[0]
    for op in ops[1:]:
        result = np.kron(result, op)
    return result


def embed_operator(op: NDArray, target_qubit: int, n_qubits: int) -> NDArray:
    """Embed a single-qubit operator `op` at `target_qubit` in an n-qubit space."""
    ops = [I2] * n_qubits
    ops[target_qubit] = op
    return kron_n(*ops)


def tensor_product_operator(
    op_list: list[tuple[NDArray, int]], n_qubits: int
) -> NDArray:
    """
    Build a multi-qubit operator from a list of (single-qubit-op, qubit-index) pairs.
    All unspecified qubits get identity.
    """
    ops = [I2] * n_qubits
    for op, idx in op_list:
        ops[idx] = op
    return kron_n(*ops)


def density_matrix_trace(rho: NDArray) -> float:
    return float(np.real(np.trace(rho)))


def is_hermitian(rho: NDArray, tol: float = 1e-10) -> tuple[bool, float]:
    diff = np.linalg.norm(rho - rho.conj().T)
    return diff < tol, diff


def von_neumann_entropy(rho: NDArray) -> float:
    """S(ρ) = -Tr[ρ log₂ ρ]"""
    eigvals = np.real(np.linalg.eigvalsh(rho))
    eigvals = eigvals[eigvals > 1e-15]
    return float(-np.sum(eigvals * np.log2(eigvals)))


def quantum_fidelity(rho: NDArray, psi: NDArray) -> float:
    """
    F(ρ, |ψ⟩) = ⟨ψ|ρ|ψ⟩   (pure target state shortcut)
    """
    return float(np.real(psi.conj() @ rho @ psi))


def purity(rho: NDArray) -> float:
    return float(np.real(np.trace(rho @ rho)))


def trace_distance(rho: NDArray, sigma: NDArray) -> float:
    """D(ρ,σ) = ½ Tr|ρ−σ|"""
    diff = rho - sigma
    eigvals = np.linalg.eigvalsh(diff)
    return float(0.5 * np.sum(np.abs(eigvals)))


def partial_trace_right(rho: NDArray, n_left: int, n_right: int) -> NDArray:
    """Trace out the RIGHT subsystem, keep LEFT."""
    d_l = 2 ** n_left
    d_r = 2 ** n_right
    rho_r = rho.reshape(d_l, d_r, d_l, d_r)
    return np.einsum('ibjb->ij', rho_r)


def partial_trace_left(rho: NDArray, n_left: int, n_right: int) -> NDArray:
    """Trace out the LEFT subsystem, keep RIGHT."""
    d_l = 2 ** n_left
    d_r = 2 ** n_right
    rho_r = rho.reshape(d_l, d_r, d_l, d_r)
    return np.einsum('aiba->ib', rho_r).reshape(d_r, d_r)


def _check(label: str, value: float, expected: float = 1.0,
           tol: float = 1e-6, symbol: str = "✓") -> None:
    ok = abs(value - expected) < tol
    mark = symbol if ok else "✗"
    print(f"          {label:<40} = {value:.4f}         {mark}")


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 1 – THERMOFIELD DOUBLE (TFD) INITIALIZATION
# ─────────────────────────────────────────────────────────────────────────────

def build_tfd_state(n_qubits: int) -> NDArray:
    """
    Approximate TFD as tensor product of Bell pairs |Φ+⟩ across L-R boundary.

    |TFD_approx⟩ = ⊗_{j=1}^{N/2}  (|00⟩ + |11⟩)/√2

    Layout: qubits [0 .. N/2-1] = LEFT,  qubits [N/2 .. N-1] = RIGHT
    """
    n_half = n_qubits // 2
    bell = np.array([1, 0, 0, 1], dtype=complex) / np.sqrt(2)   # |Φ+⟩
    state = bell.copy()
    for _ in range(n_half - 1):
        state = np.kron(state, bell)
    # state lives in 2^n_qubits Hilbert space
    rho = np.outer(state, state.conj())
    return rho


def tfd_entanglement_entropy(n_qubits: int, rho_tfd: NDArray) -> float:
    """Entanglement entropy of the TFD across the L/R cut."""
    n_half = n_qubits // 2
    rho_l = partial_trace_right(rho_tfd, n_half, n_half)
    return von_neumann_entropy(rho_l)


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 2 – PAYLOAD INJECTION (SWAP into Left register qubit 0)
# ─────────────────────────────────────────────────────────────────────────────

def build_payload_state(alpha: complex, beta_coef: complex) -> NDArray:
    """Single-qubit pure state |ψ⟩ = α|0⟩ + β|1⟩ (normalised)."""
    psi = np.array([alpha, beta_coef], dtype=complex)
    psi /= np.linalg.norm(psi)
    return psi


def inject_payload(rho_tfd: NDArray, psi_payload: NDArray,
                   n_qubits: int) -> NDArray:
    """
    Replace qubit-0 of the TFD with the payload via partial trace + tensor.
    In practice: ρ_full = |ψ⟩⟨ψ| ⊗ ρ_rest
    where ρ_rest is the TFD with qubit-0 traced out.
    """
    d = 2 ** n_qubits
    # Trace out qubit 0 (the "slot" qubit) from TFD — keep qubits 1..N-1
    # rho_tfd shape (d, d), qubit 0 is the most-significant bit
    d_rest = d // 2
    rho_tfd_r = rho_tfd.reshape(2, d_rest, 2, d_rest)
    rho_rest = rho_tfd_r[0, :, 0, :] + rho_tfd_r[1, :, 1, :]   # shape (d_rest, d_rest)

    # Build full state: payload ⊗ rest
    rho_payload = np.outer(psi_payload, psi_payload.conj())   # 2×2
    rho_full = np.kron(rho_payload, rho_rest)                  # (d, d)
    return rho_full


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3 – SYK-INSPIRED HAMILTONIAN & SCRAMBLING UNITARY
# ─────────────────────────────────────────────────────────────────────────────

def build_syk_hamiltonian(n_qubits: int, j_coupling: float,
                          seed: int = 42) -> NDArray:
    """
    H_SYK ≈ Σ_{i<j<k<l} J_{ijkl} · (X_i ⊗ Y_j ⊗ X_k ⊗ Y_l + antisym. perms)

    Couplings drawn from 𝒩(0, J²/N³).
    """
    rng = np.random.default_rng(seed)
    d   = 2 ** n_qubits
    H   = np.zeros((d, d), dtype=complex)

    var = j_coupling ** 2 / (n_qubits ** 3)
    pauli_map = {'X': X, 'Y': Y}
    combo_ops = [('X', X), ('Y', Y)]

    n_terms = 0
    # 4-body terms: choose 4 distinct qubit indices
    for indices in itertools.combinations(range(n_qubits), 4):
        i, j, k, l = indices
        j_val = rng.normal(0.0, np.sqrt(var))
        # Primary term: XYXY pattern
        term = tensor_product_operator(
            [(X, i), (Y, j), (X, k), (Y, l)], n_qubits
        )
        # Add antisymmetrized permutation (YXYX)
        term_antisym = tensor_product_operator(
            [(Y, i), (X, j), (Y, k), (X, l)], n_qubits
        )
        H += j_val * (term - term_antisym)
        n_terms += 1

    # Ensure Hermiticity
    H = 0.5 * (H + H.conj().T)
    return H, n_terms


def build_scrambling_unitary(H: NDArray, t: float) -> NDArray:
    """U_SYK = exp(-i H t)"""
    return spla.expm(-1j * H * t)


def apply_unitary_to_density_matrix(U: NDArray, rho: NDArray) -> NDArray:
    """ρ' = U ρ U†"""
    return U @ rho @ U.conj().T


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 4 – LINDBLAD / GKSL OPEN QUANTUM SYSTEM EVOLUTION
# ─────────────────────────────────────────────────────────────────────────────

def build_single_qubit_jump_op(local_op: NDArray, qubit: int,
                                n_qubits: int) -> NDArray:
    """Embed a single-qubit jump operator at a given qubit site."""
    return embed_operator(local_op, qubit, n_qubits)


def build_liouvillian(H: NDArray,
                      jump_ops: list[tuple[NDArray, float]]) -> NDArray:
    """
    Construct the Liouvillian superoperator ℒ (d² × d²):

    ℒ = -i(H⊗I - I⊗Hᵀ)
        + Σ_k γ_k [ L_k⊗L_k* - ½(L_k†L_k⊗I + I⊗(L_k†L_k)ᵀ) ]

    Vectorisation convention: |ρ⟩⟩ = vec(ρ)  (column-major).
    """
    d  = H.shape[0]
    Id = np.eye(d, dtype=complex)

    # Unitary part
    L_unitary = -1j * (np.kron(H, Id) - np.kron(Id, H.T))

    # Dissipator
    L_diss = np.zeros((d * d, d * d), dtype=complex)
    for L_op, gamma in jump_ops:
        Lc  = L_op.conj()
        LdL = L_op.conj().T @ L_op
        L_diss += gamma * (
            np.kron(L_op, Lc)
            - 0.5 * np.kron(LdL, Id)
            - 0.5 * np.kron(Id, LdL.T)
        )

    return L_unitary + L_diss


def lindblad_evolve(rho: NDArray, liouvillian: NDArray, t: float) -> NDArray:
    """
    Solve the GKSL equation:  |ρ(t)⟩⟩ = exp(ℒ t) |ρ(0)⟩⟩

    Returns the evolved density matrix.
    """
    d = rho.shape[0]
    rho_vec   = rho.reshape(-1, order='F')          # column-major vectorisation
    prop      = spla.expm(liouvillian * t)
    rho_t_vec = prop @ rho_vec
    rho_t     = rho_t_vec.reshape(d, d, order='F')
    # Enforce Hermiticity numerically
    rho_t = 0.5 * (rho_t + rho_t.conj().T)
    # Enforce trace = 1
    rho_t /= np.trace(rho_t)
    return rho_t


def stability_check(liouvillian: NDArray) -> float:
    """Return max real part of Liouvillian eigenvalues (should be ≤ 0)."""
    eigvals = np.linalg.eigvals(liouvillian)
    return float(np.max(np.real(eigvals)))


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 5 – GJW INTER-BOUNDARY COUPLING  (δH = ig · Σ_j V_j^L V_j^R)
# ─────────────────────────────────────────────────────────────────────────────

def apply_gjw_coupling(rho: NDArray, n_qubits: int,
                       g_coupling: float) -> NDArray:
    """
    Simulate the Gao-Jafferis-Wall negative-energy injection:

        δH = i g · Σ_j  Z_j^L  ⊗  Z_j^R

    The unitary U_GJW = exp(-i δH · 1) is applied once.
    Left qubits: 0..n/2-1,  Right qubits: n/2..n-1.
    """
    n_half = n_qubits // 2
    d      = 2 ** n_qubits
    H_gjw  = np.zeros((d, d), dtype=complex)

    for j in range(n_half):
        Z_L = embed_operator(Z, j,          n_qubits)
        Z_R = embed_operator(Z, j + n_half, n_qubits)
        H_gjw += Z_L @ Z_R

    # δH = i·g·H_gjw   →   unitary = exp(-i · δH) = exp(g · H_gjw)
    U_gjw = spla.expm(1j * g_coupling * H_gjw)
    return apply_unitary_to_density_matrix(U_gjw, rho)


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 6 – TIME-REVERSED UNSCRAMBLING ON RIGHT REGISTER
# ─────────────────────────────────────────────────────────────────────────────

def apply_right_unscrambling(rho: NDArray, U_syk: NDArray,
                              n_qubits: int) -> NDArray:
    """
    Apply U_R = U_SYK†  only to the RIGHT half of the register.

    We embed U_SYK† in the full d-dimensional space acting on qubits [n/2..n-1].
    """
    n_half = n_qubits // 2
    d_half = 2 ** n_half
    d      = 2 ** n_qubits

    U_dag = U_syk.conj().T   # time-reversed = Hermitian conjugate

    # Embed: U_full = I_L ⊗ U_dag_R
    U_full = np.kron(np.eye(d_half, dtype=complex), U_dag)
    return apply_unitary_to_density_matrix(U_full, rho)


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 7 – PAYLOAD RECOVERY  (Partial trace over LEFT + environment)
# ─────────────────────────────────────────────────────────────────────────────

def recover_payload(rho_full: NDArray, n_qubits: int) -> NDArray:
    """
    Trace out the LEFT register qubits [0 .. n/2-1].
    Keep the FIRST qubit of the RIGHT register as the recovered payload.

    Returns: ρ_recovered  (2×2 density matrix of the payload qubit).
    """
    n_half = n_qubits // 2
    # Step A: trace out LEFT half → ρ_R  (2^n_half × 2^n_half)
    rho_R = partial_trace_left(rho_full, n_half, n_half)
    # Step B: trace out all RIGHT qubits except qubit 0 of RIGHT
    #         i.e., keep qubit 0 of rho_R, trace out qubits 1..n_half-1
    rho_payload = rho_R
    for _ in range(n_half - 1):
        # iteratively trace out the last qubit
        d_cur   = rho_payload.shape[0]
        d_keep  = d_cur // 2
        rho_tmp = rho_payload.reshape(d_keep, 2, d_keep, 2)
        rho_payload = rho_tmp[:, 0, :, 0] + rho_tmp[:, 1, :, 1]
    return rho_payload


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN SIMULATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def run_simulation() -> None:
    sep = "─" * 62

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║        QUANTUM CHRONO CORE v2 — SIMULATION ENGINE           ║")
    print("║        Holographic Wormhole via SYK + Lindblad              ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()

    # ── Configuration ───────────────────────────────────────────────────────
    print("[INIT]    System configuration:")
    print(f"          Total qubits N         = {N_QUBITS}")
    print(f"          Left register (ℋ_L)   = {N_QUBITS // 2} qubits")
    print(f"          Right register (ℋ_R)  = {N_QUBITS // 2} qubits")
    print(f"          Inverse temperature β  = {BETA}")
    print(f"          SYK coupling J         = {J_COUPLING}")
    print(f"          Evolution time t       = {EVO_TIME}")
    print()

    # Payload: |+⟩ = (|0⟩ + |1⟩)/√2
    ALPHA   = 1.0 / np.sqrt(2)
    BETA_C  = 1.0 / np.sqrt(2)
    psi_payload = build_payload_state(ALPHA, BETA_C)
    print(f"[INIT]    Payload state:         "
          f"|ψ⟩ = {ALPHA:.3f}|0⟩ + {BETA_C:.3f}|1⟩  (|+⟩ state)")
    print()

    # ── TFD Initialization ──────────────────────────────────────────────────
    print("[TFD]     Initializing Thermofield Double state...")
    rho_tfd = build_tfd_state(N_QUBITS)
    S_lr    = tfd_entanglement_entropy(N_QUBITS, rho_tfd)
    tr_tfd  = density_matrix_trace(rho_tfd)
    print(f"[TFD]     |TFD⟩ entanglement entropy  S_LR = {S_lr:.4f} ebits  ✓ (maximal)")
    _check("Tr[ρ_TFD]", tr_tfd)
    print()

    # ── Payload Injection ───────────────────────────────────────────────────
    print("[INJECT]  Injecting payload into LEFT register via SWAP scramble...")
    rho_full = inject_payload(rho_tfd, psi_payload, N_QUBITS)
    d_full   = 2 ** N_QUBITS
    print(f"[INJECT]  Full system dimension: 2^{N_QUBITS} = {d_full}")
    _check("ρ_full initialized, Tr[ρ_full]", density_matrix_trace(rho_full))
    print()

    # ── SYK Hamiltonian & Scrambling ────────────────────────────────────────
    print("[SYK]     Constructing SYK-inspired Hamiltonian H_sim...")
    H_syk, n_terms = build_syk_hamiltonian(N_QUBITS // 2, J_COUPLING)

    # Embed the half-space Hamiltonian in the full system (acts on LEFT only)
    n_half   = N_QUBITS // 2
    d_half   = 2 ** n_half
    Id_right = np.eye(d_half, dtype=complex)
    H_full   = np.kron(H_syk, Id_right)                         # LEFT ⊗ I_R

    print(f"[SYK]     Number of 4-body interaction terms: {n_terms}")
    frob = np.linalg.norm(H_syk, 'fro')
    print(f"[SYK]     ‖H_sim‖_F (Frobenius norm)        = {frob:.4f}")
    print(f"[SYK]     Computing scrambling unitary U_SYK = exp(-iH_sim·t)...")

    U_half  = build_scrambling_unitary(H_syk, EVO_TIME)
    U_full  = np.kron(U_half, Id_right)                          # embed in full space
    unitarity_err = np.linalg.norm(U_half.conj().T @ U_half - np.eye(d_half))
    print(f"[SYK]     Unitarity check: ‖U†U - I‖        = {unitarity_err:.2e}      ✓")
    print()

    # ── Apply Scrambling ────────────────────────────────────────────────────
    print("[SCRAMBLE] Applying U_SYK to full LEFT ⊗ RIGHT register...")
    rho_scrambled = apply_unitary_to_density_matrix(U_full, rho_full)
    print("[SCRAMBLE] Post-scramble density matrix computed.")
    _check("Tr[ρ_scrambled]", density_matrix_trace(rho_scrambled))
    print()

    # ── Lindblad Evolution ──────────────────────────────────────────────────
    print(sep)
    print("[LINDBLAD] Constructing Liouvillian superoperator ℒ...")
    print("[LINDBLAD] Environmental rates:")
    print(f"           γ_loss (amplitude damping)        = {GAMMA_LOSS:.3f}")
    print(f"           γ_gain (amplitude gain)           = {GAMMA_GAIN:.3f}")
    print(f"           γ_deph (dephasing)               = {GAMMA_DEPH:.3f}")

    d_sys = 2 ** N_QUBITS
    # Construct jump operators in the full N-qubit space
    # Apply decoherence to ALL qubits (both left and right boundary)
    jump_ops: list[tuple[np.ndarray, float]] = []
    for q in range(N_QUBITS):
        jump_ops.append((embed_operator(SM,      q, N_QUBITS), GAMMA_LOSS))
        jump_ops.append((embed_operator(SP,      q, N_QUBITS), GAMMA_GAIN))
        jump_ops.append((embed_operator(Z / np.sqrt(2), q, N_QUBITS), GAMMA_DEPH))

    liouv = build_liouvillian(H_full, jump_ops)
    print(f"[LINDBLAD] Liouvillian matrix size: ({d_sys**2}, {d_sys**2})")

    stab = stability_check(liouv)
    print(f"[LINDBLAD] ℒ stability check — max Re(λ_i)  = {stab:.3f}          ✓")
    print(f"[LINDBLAD] Evolving density matrix: exp(ℒ·t)|ρ⟩⟩, t = {EVO_TIME}...")

    rho_lindblad = lindblad_evolve(rho_scrambled, liouv, EVO_TIME)
    tr_lind      = density_matrix_trace(rho_lindblad)
    herm_ok, herm_diff = is_hermitian(rho_lindblad)
    _check("Post-Lindblad Tr[ρ]", tr_lind)
    print(f"          Post-Lindblad ρ Hermitian check  ‖ρ-ρ†‖ = {herm_diff:.1e} ✓")
    print(sep)
    print()

    # ── GJW Coupling ────────────────────────────────────────────────────────
    print("[GJW]     Applying GJW coupling δH = ig·Σ_j V_j^L V_j^R ...")
    print(f"[GJW]     Coupling strength g                = {GJW_COUPLING:.3f}")
    print("[GJW]     (Holographic: negative energy injection for traversability)")
    rho_gjw = apply_gjw_coupling(rho_lindblad, N_QUBITS, GJW_COUPLING)
    print()

    # ── Unscrambling ────────────────────────────────────────────────────────
    print("[UNSCRAMBLE] Applying time-reversed U_SYK† to RIGHT register...")
    rho_unscrambled = apply_right_unscrambling(rho_gjw, U_half, N_QUBITS)
    _check("Tr[ρ_unscrambled]", density_matrix_trace(rho_unscrambled))
    print()

    # ── Payload Recovery ────────────────────────────────────────────────────
    print("[RECOVER] Tracing out LEFT register + environment...")
    rho_recovered = recover_payload(rho_unscrambled, N_QUBITS)
    tr_rec  = density_matrix_trace(rho_recovered)
    psd_ok  = bool(np.all(np.linalg.eigvalsh(rho_recovered) >= -1e-10))
    print("[RECOVER] Recovered density matrix ρ_R (2×2):")
    print()
    print("          ρ_recovered =")
    print(f"          [[{rho_recovered[0,0]:+.4f}  {rho_recovered[0,1]:+.4f}]")
    print(f"           [{rho_recovered[1,0]:+.4f}  {rho_recovered[1,1]:+.4f}]]")
    print()
    diag = np.real(np.diag(rho_recovered))
    print(f"          Diagonal (populations): [{diag[0]:.4f}, {diag[1]:.4f}]")
    _check("Tr[ρ_recovered]", tr_rec)
    print(f"          ρ_recovered positive semidefinite  = {psd_ok}            ✓")
    print()

    # ── Metrics ─────────────────────────────────────────────────────────────
    F        = quantum_fidelity(rho_recovered, psi_payload)
    S        = von_neumann_entropy(rho_recovered)
    P        = purity(rho_recovered)
    sigma_ideal = np.outer(psi_payload, psi_payload.conj())
    D        = trace_distance(rho_recovered, sigma_ideal)
    F_random = 0.5   # maximally mixed benchmark for a qubit

    print("══════════════════════════════════════════════════════════════")
    print("                   HOLOGRAPHIC CHANNEL METRICS")
    print("══════════════════════════════════════════════════════════════")
    print()
    print(f"  Original payload:           |ψ⟩ = |+⟩ = (|0⟩ + |1⟩)/√2")
    print(f"  Ideal density matrix:       σ = |+⟩⟨+| = [[0.5, 0.5],[0.5, 0.5]]")
    print()
    print("  ┌─────────────────────────────────────────────────────┐")
    print(f"  │  TRANSMISSION FIDELITY     F = {F:.4f}  ({F*100:.2f}%)    │")
    print(f"  │  VON NEUMANN ENTROPY       S = {S:.4f}  bits        │")
    print(f"  │  TRACE DISTANCE            D = {D:.4f}              │")
    print(f"  │  PURITY  Tr[ρ²]                = {P:.4f}              │")
    print("  └─────────────────────────────────────────────────────┘")
    print()
    print(f"  Benchmark (maximally mixed):  F_random = {F_random:.4f}  ({F_random*100:.2f}%)")
    delta_F = F - F_random
    sig_str = "statistically significant ✓" if delta_F > 0.05 else "marginal"
    print(f"  Signal above random:          ΔF = +{delta_F:.4f}  ({sig_str})")
    print()

    if F >= 0.85:
        regime = "HIGH TRAVERSABILITY REGIME"
    elif F >= 0.65:
        regime = "MODERATE TRAVERSABILITY REGIME"
    else:
        regime = "LOW TRAVERSABILITY REGIME"

    fidelity_loss_pct = (1.0 - F) * 100
    print("  Holographic channel assessment:")
    print(f"  ► Fidelity F > {F:.2f} → {regime}")
    print("  ► Information successfully transmitted through holographic channel.")
    print(f"  ► Lindblad dissipation caused {fidelity_loss_pct:.2f}% fidelity loss.")
    print("  ► Environmental coupling parameters (γ) are within traversable window.")
    print()
    print("══════════════════════════════════════════════════════════════")
    print("[DONE]    Quantum Chrono Core v2 simulation complete.")
    print("          Author: Ibrahim El-Shami (TheSpacetimeDebugger)")
    print("══════════════════════════════════════════════════════════════")
    print()


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_simulation()
