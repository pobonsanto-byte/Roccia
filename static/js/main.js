// ===== FUNÇÕES GERAIS =====

// Inicializa tooltips
document.addEventListener('DOMContentLoaded', function() {
    // Tooltips do Bootstrap
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
    
    // Popovers do Bootstrap
    var popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    var popoverList = popoverTriggerList.map(function (popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });
    
    // Auto-fechar alerts após 5 segundos
    setTimeout(function() {
        var alerts = document.querySelectorAll('.alert:not(.alert-permanent)');
        alerts.forEach(function(alert) {
            var bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        });
    }, 5000);
    
    // Formatação de números
    formatNumbers();
    
    // Inicializa gráficos se existirem
    initCharts();
    
    // Aplica máscaras de entrada
    applyInputMasks();
    
    // Formata datas
    formatAllDates();
});

// Formata números grandes
function formatNumbers() {
    document.querySelectorAll('.format-number').forEach(function(element) {
        var number = parseInt(element.textContent);
        if (number >= 1000000) {
            element.textContent = (number / 1000000).toFixed(1) + 'M';
        } else if (number >= 1000) {
            element.textContent = (number / 1000).toFixed(1) + 'K';
        }
    });
}

// Formata datas
function formatAllDates() {
    document.querySelectorAll('.format-date').forEach(function(element) {
        var dateStr = element.textContent;
        if (dateStr) {
            try {
                var date = new Date(dateStr);
                element.textContent = date.toLocaleDateString('pt-BR', {
                    day: '2-digit',
                    month: '2-digit',
                    year: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit'
                });
            } catch (e) {
                console.log('Erro ao formatar data:', e);
            }
        }
    });
}

// Modal de confirmação
function confirmAction(message, callback) {
    const modalHtml = `
        <div class="modal fade" id="confirmModal" tabindex="-1">
            <div class="modal-dialog">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">Confirmar ação</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <p>${message}</p>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-outline" data-bs-dismiss="modal">Cancelar</button>
                        <button type="button" class="btn btn-danger" id="confirmButton">Confirmar</button>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    const modal = new bootstrap.Modal(document.getElementById('confirmModal'));
    modal.show();
    
    document.getElementById('confirmButton').addEventListener('click', function() {
        callback();
        modal.hide();
    });
    
    document.getElementById('confirmModal').addEventListener('hidden.bs.modal', function() {
        this.remove();
    });
}

// Carrega estatísticas em tempo real
function loadRealTimeStats() {
    fetch('/api/stats')
        .then(response => response.json())
        .then(data => {
            document.querySelectorAll('[data-stat]').forEach(element => {
                const statName = element.getAttribute('data-stat');
                if (data[statName] !== undefined) {
                    element.textContent = data[statName].toLocaleString();
                }
            });
        })
        .catch(error => console.error('Erro ao carregar stats:', error));
}

// Atualiza estatísticas a cada 30 segundos
if (document.querySelector('[data-stat]')) {
    setInterval(loadRealTimeStats, 30000);
    loadRealTimeStats();
}

// Sidebar toggle para mobile
function toggleSidebar() {
    document.querySelector('.sidebar').classList.toggle('show');
}

// Fecha sidebar ao clicar fora (mobile)
document.addEventListener('click', function(event) {
    const sidebar = document.querySelector('.sidebar');
    const sidebarToggle = document.querySelector('.sidebar-toggle');
    
    if (window.innerWidth <= 768 && 
        sidebar && sidebar.classList.contains('show') && 
        !sidebar.contains(event.target) && 
        (!sidebarToggle || !sidebarToggle.contains(event.target))) {
        sidebar.classList.remove('show');
    }
});

// Botão toggle para mobile
const sidebarToggleBtn = document.querySelector('.sidebar-toggle');
if (sidebarToggleBtn) {
    sidebarToggleBtn.addEventListener('click', toggleSidebar);
}

// Copiar para área de transferência
function copyToClipboard(text, element = null) {
    navigator.clipboard.writeText(text).then(function() {
        if (element) {
            const originalText = element.innerHTML;
            element.innerHTML = '<i class="bi bi-check"></i> Copiado!';
            element.classList.add('btn-success');
            
            setTimeout(function() {
                element.innerHTML = originalText;
                element.classList.remove('btn-success');
            }, 2000);
        } else {
            showToast('Copiado para área de transferência!', 'success');
        }
    }).catch(function(err) {
        showToast('Erro ao copiar: ' + err, 'error');
    });
}

// Mostrar toast
function showToast(message, type = 'info') {
    const toastId = 'toast-' + Date.now();
    const typeClass = type === 'success' ? 'text-bg-success' : 
                     type === 'error' ? 'text-bg-danger' : 
                     type === 'warning' ? 'text-bg-warning' : 'text-bg-info';
    
    const toastHtml = `
        <div id="${toastId}" class="toast ${typeClass}" role="alert">
            <div class="d-flex">
                <div class="toast-body">
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>
    `;
    
    // Cria container se não existir
    let container = document.querySelector('.toast-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'toast-container position-fixed bottom-0 end-0 p-3';
        document.body.appendChild(container);
    }
    
    container.insertAdjacentHTML('beforeend', toastHtml);
    const toastElement = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastElement);
    toast.show();
    
    toastElement.addEventListener('hidden.bs.toast', function() {
        this.remove();
    });
}

// Validação de formulário
function validateForm(form) {
    let isValid = true;
    const requiredFields = form.querySelectorAll('[required]');
    
    requiredFields.forEach(field => {
        if (!field.value.trim()) {
            field.classList.add('is-invalid');
            isValid = false;
        } else {
            field.classList.remove('is-invalid');
        }
    });
    
    return isValid;
}

// Máscaras de entrada
function applyInputMasks() {
    // ID numérico
    document.querySelectorAll('input[data-mask="id"]').forEach(input => {
        input.addEventListener('input', function(e) {
            this.value = this.value.replace(/\D/g, '');
        });
    });
    
    // URL
    document.querySelectorAll('input[data-mask="url"]').forEach(input => {
        input.addEventListener('blur', function(e) {
            if (this.value && !this.value.startsWith('http')) {
                this.value = 'https://' + this.value;
            }
        });
    });
}

// Filtro de tabela
function filterTable(tableId, searchId) {
    const searchInput = document.getElementById(searchId);
    if (!searchInput) return;
    
    const table = document.getElementById(tableId);
    if (!table) return;
    
    const rows = table.querySelectorAll('tbody tr');
    
    searchInput.addEventListener('input', function() {
        const searchTerm = this.value.toLowerCase();
        
        rows.forEach(row => {
            const text = row.textContent.toLowerCase();
            row.style.display = text.includes(searchTerm) ? '' : 'none';
        });
    });
}

// Ordenação de tabela
function sortTable(tableId, columnIndex) {
    const table = document.getElementById(tableId);
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    
    const isNumeric = rows.every(row => {
        const cell = row.cells[columnIndex];
        return !isNaN(cell.textContent);
    });
    
    const sortedRows = rows.sort((a, b) => {
        const aVal = a.cells[columnIndex].textContent;
        const bVal = b.cells[columnIndex].textContent;
        
        if (isNumeric) {
            return parseFloat(aVal) - parseFloat(bVal);
        } else {
            return aVal.localeCompare(bVal);
        }
    });
    
    // Alterna entre ascendente/descendente
    if (table.dataset.sortColumn === columnIndex.toString()) {
        sortedRows.reverse();
        table.dataset.sortDirection = table.dataset.sortDirection === 'asc' ? 'desc' : 'asc';
    } else {
        table.dataset.sortColumn = columnIndex;
        table.dataset.sortDirection = 'asc';
    }
    
    // Atualiza a tabela
    sortedRows.forEach(row => tbody.appendChild(row));
    
    // Atualiza indicadores
    updateSortIndicators(tableId, columnIndex);
}

function updateSortIndicators(tableId, columnIndex) {
    const table = document.getElementById(tableId);
    const headers = table.querySelectorAll('th');
    
    headers.forEach((header, index) => {
        header.classList.remove('sort-asc', 'sort-desc');
        if (index === columnIndex) {
            header.classList.add(
                table.dataset.sortDirection === 'asc' ? 'sort-asc' : 'sort-desc'
            );
        }
    });
}

// Inicializar gráficos
function initCharts() {
    // Exemplo: Gráfico de XP
    const xpChart = document.getElementById('xpChart');
    if (xpChart && typeof Chart !== 'undefined') {
        const ctx = xpChart.getContext('2d');
        new Chart(ctx, {
            type: 'line',
            data: {
                labels: ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun'],
                datasets: [{
                    label: 'XP Ganho',
                    data: [1200, 1900, 3000, 5000, 2000, 3000],
                    borderColor: '#5865F2',
                    backgroundColor: 'rgba(88, 101, 242, 0.1)',
                    tension: 0.4
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: {
                        labels: {
                            color: '#B9BBBE'
                        }
                    }
                },
                scales: {
                    y: {
                        grid: {
                            color: '#424549'
                        },
                        ticks: {
                            color: '#B9BBBE'
                        }
                    },
                    x: {
                        grid: {
                            color: '#424549'
                        },
                        ticks: {
                            color: '#B9BBBE'
                        }
                    }
                }
            }
        });
    }
}

// Formata tamanho de arquivo
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// AJAX Helper
function ajaxRequest(url, method = 'GET', data = null) {
    return fetch(url, {
        method: method,
        headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
        },
        body: data ? JSON.stringify(data) : null
    })
    .then(response => {
        if (!response.ok) throw new Error('Erro na requisição');
        return response.json();
    });
}

// Confirmação antes de deletar
function confirmDelete(message, callback) {
    return confirmAction(message || 'Tem certeza que deseja excluir este item?', callback);
}

// Adicionar listener para botões de deletar
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('btn-delete') || 
        e.target.closest('.btn-delete')) {
        const button = e.target.classList.contains('btn-delete') ? 
                      e.target : e.target.closest('.btn-delete');
        const message = button.dataset.confirm || 'Tem certeza que deseja excluir este item?';
        
        e.preventDefault();
        confirmDelete(message, function() {
            if (button.form) {
                button.form.submit();
            } else if (button.href) {
                window.location.href = button.href;
            }
        });
    }
});

// ===== EXPORTS =====
window.ImunePanel = {
    confirmAction,
    copyToClipboard,
    showToast,
    toggleSidebar,
    filterTable,
    sortTable,
    ajaxRequest,
    formatFileSize,
    confirmDelete
};

// Inicializa filtros de tabela se existirem
document.addEventListener('DOMContentLoaded', function() {
    const tables = document.querySelectorAll('table[data-filter]');
    tables.forEach(table => {
        const filterId = table.getAttribute('data-filter');
        filterTable(table.id, filterId);
    });
});
